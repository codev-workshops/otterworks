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

3. Inspect the failing S3 call. Each failure logs `S3 operation failed` with the
   `op`, `bucket`, `key`, and the full SDK error (including the AWS error code):
   ```
   kubectl logs -l app=file-service --tail=500 -n otterworks | grep "S3 operation failed"
   ```
   - `bucket=otterworks-files-chaos-nonexistent` + `NoSuchBucket` -> the chaos flag is the cause.
   - Any other bucket + `NoSuchBucket` -> `S3_BUCKET` is misconfigured (see the
     `file-bad-bucket` scenario in `scripts/bug-catalog.yaml`); compare against
     `kubectl get configmap -n otterworks -o yaml | grep S3_BUCKET`.
   - `AccessDenied` -> IAM/IRSA role for file-service lacks `s3:PutObject` on the bucket.
4. Confirm reads are unaffected (only uploads route through the chaos bucket):
   `GET /api/v1/files` and `GET /api/v1/files/{id}/download` should still return 200.

## Resolution Steps

1. **Chaos flag set** -- clear it (any one of):
   ```
   redis-cli DEL chaos:file-service:upload_s3_error
   curl -X DELETE http://admin-service:8089/api/v1/admin/chaos -H "X-Chaos-Secret: $CHAOS_SECRET"
   ./scripts/inject-bug.sh <ATTENDEE_ID> reset          # per-tenant deployments
   ```
   The flag is read on every upload, so no restart is needed. Flags set through
   admin-service also auto-expire after 10 minutes.
2. **Misconfigured `S3_BUCKET`** -- restore the correct bucket name in the file-service
   ConfigMap / Helm values and `kubectl rollout restart deploy/file-service -n otterworks`.
3. **IAM** -- restore the file-service role policy, then retry.
4. Verify recovery: a test upload returns `201`, and the `FileUploadHighErrorRate`
   alert resolves once the 5xx ratio drops below 10%.

## Post-Incident

- Uploads that failed were rejected before any metadata was written, so there is no
  orphaned DynamoDB state to clean up; affected users just need to retry.
- Record who set the chaos flag / changed the config and when (admin-service logs
  `CHAOS TRIGGERED: <key>`), and resolve the corresponding incident in admin-service.
