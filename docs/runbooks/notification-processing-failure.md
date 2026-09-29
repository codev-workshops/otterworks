# Runbook: Notification Processing Failures

**Severity:** Critical

## Alert

`NotificationConsumerProcessingErrors` -- fires when the notification-service SQS consumer is generating sustained processing errors.

## Symptoms

- Users stop receiving notifications (email, in-app) for shared files and document updates.
- The Chaos Scenarios dashboard shows non-zero processing error rate on the notification-service panel.
- SQS queue depth grows unboundedly as failed messages re-enter after visibility timeout.

## Investigation Steps

1. Check notification-service consumer logs for deserialization errors:
   ```
   kubectl logs -l app=notification-service --tail=100 -n otterworks | grep -i "deseriali\|timestamp\|schema"
   ```
2. Check whether the chaos flag `chaos:notification-service:consumer_strict_schema` is set in Redis:
   ```
   redis-cli EXISTS chaos:notification-service:consumer_strict_schema
   ```

3. Inspect queue depth and the dead-letter queue:
   ```
   aws sqs get-queue-attributes --queue-url <notifications-queue-url> \
     --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
   aws sqs get-queue-attributes --queue-url <notifications-dlq-url> \
     --attribute-names ApproximateNumberOfMessages
   ```
4. Sample a failing message and check its `timestamp` field. Legacy producers emit Unix
   epoch integers (e.g. `1704067200`) instead of RFC 3339 strings.

## Resolution Steps

1. Immediate mitigation: clear the chaos flag if it is set.
   ```
   redis-cli DEL chaos:notification-service:consumer_strict_schema
   ```
2. Permanent fix: the consumer always uses the lenient parser and normalizes `timestamp`
   (RFC 3339 string, or epoch seconds/milliseconds) via `FlexibleTimestampSerializer`.
   Deploy a notification-service build that includes it.
3. Once the error rate returns to zero, redrive the DLQ back to the main queue:
   ```
   aws sqs start-message-move-task --source-arn <notifications-dlq-arn>
   ```

## Post-Incident

- Confirm `notifications_processing_errors_total` rate is 0 and queue depth is draining.
- Verify the notifications queue has a redrive policy (`maxReceiveCount` 3) so poison
  messages cannot cycle forever.
