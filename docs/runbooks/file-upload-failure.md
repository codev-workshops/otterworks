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

3. Compare the bucket the pod is configured with against the bucket Terraform created:
   ```
   kubectl get deploy file-service -n otterworks -o jsonpath='{.spec.template.spec.containers[0].env}' | grep -o '"S3_BUCKET"[^}]*'
   terraform -chdir=infrastructure/terraform output s3_file_bucket
   ```
4. Check recent Helm history for a config override on the release:
   ```
   helm history file-service -n otterworks
   helm get values file-service -n otterworks | grep S3_BUCKET
   ```

## Resolution Steps

1. If `S3_BUCKET` does not match the Terraform output, redeploy with the correct value
   (`scripts/deploy-dev.sh` / `scripts/deploy-tenant.sh` derive it from Terraform outputs),
   or `helm rollback file-service <REVISION> -n otterworks` to the last good revision.
2. If the chaos flag is set, clear it: `scripts/inject-bug.sh <ID> reset`
   (or `redis-cli DEL chaos:file-service:upload_s3_error`).
3. Confirm recovery: the `FileUploadHighErrorRate` alert resolves and an upload via
   `POST /api/v1/files` returns 201.

## Post-Incident

<!-- TODO -->
