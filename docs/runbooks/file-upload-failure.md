# Runbook: File Upload Failures

**Severity:** High

## Alert

`FileUploadHighErrorRate` -- fires when file-service 5xx rate exceeds 10% over a 1-minute window.

## Symptoms

- Users cannot upload files; the UI shows generic upload error messages.
- The Chaos Scenarios dashboard shows elevated error rates on the file-service panel.
- Application logs contain `NoSuchBucket` errors from the AWS S3 SDK.

## Investigation Steps

1. Confirm the error in file-service logs:
   ```
   kubectl logs -l app=file-service --tail=100 -n otterworks | grep -i "NoSuchBucket\|S3\|500"
   ```
2. Check whether the chaos flag `chaos:file-service:upload_s3_error` is set in Redis:
   ```
   redis-cli EXISTS chaos:file-service:upload_s3_error
   ```

3. Read the S3 error in the upload response / error log. It names the bucket and
   the AWS error code, e.g.:
   ```
   upload failed (bucket=otterworks-files-chaos-nonexistent, code=NoSuchBucket): The specified bucket does not exist
   ```
   - `bucket=otterworks-files-chaos-nonexistent` together with the log line
     `Chaos flag active: redirecting upload to nonexistent bucket` means the chaos
     flag is the cause. `S3_BUCKET` is not being used for these requests.
   - Any other bucket name means `S3_BUCKET` really is wrong. Compare it with
     Terraform (`otterworks-files-<env>`; locally `otterworks-files`):
     ```
     kubectl get configmap file-service-config -n otterworks -o jsonpath='{.data.S3_BUCKET}'
     terraform -chdir=infrastructure/terraform output -raw s3_file_bucket
     aws s3api head-bucket --bucket <name>
     ```

## Resolution Steps

**Chaos flag set.** Clear it. Uploads recover on the next request, and no restart is needed.
Pick one of these:
- Admin dashboard -> Incidents -> reset chaos, or `DELETE /api/v1/admin/chaos`
  through the gateway (send `X-Chaos-Secret` when `CHAOS_SECRET` is set). This also
  resolves the open chaos incidents.
- `redis-cli DEL chaos:file-service:upload_s3_error`
- Tenant: `scripts/inject-bug.sh <ATTENDEE_ID> reset`

The flag also expires by itself when its TTL runs out (600s from the admin API, 3600s
from `inject-bug.sh`).

**`S3_BUCKET` is wrong** (for example the `file-bad-bucket` scenario, or a deploy
where the Terraform output came back empty). Redeploy file-service with the Terraform
bucket, using `scripts/deploy-dev.sh` or `scripts/deploy-tenant.sh <ID>`, or run
`helm upgrade --reuse-values --set-string config.S3_BUCKET=<bucket>` and then
`kubectl rollout restart deploy/file-service`.

**Verify:** a test upload returns `201`, and the 5xx ratio on the file-service panel
goes back under 10% (the alert clears after 1m).

## Post-Incident

- Confirm there are no `NoSuchBucket` errors after the fix time. Uploads that failed
  were never written to S3 or DynamoDB, so there is nothing to clean up, but users
  have to retry them.
- If `S3_BUCKET` was the cause, find out why the deploy produced it (for example a
  failed `terraform output`) before closing.
