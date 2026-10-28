import pino, { type DestinationStream, type Logger } from 'pino';
import { trace } from '@opentelemetry/api';
import { currentRequestId } from './request-context';

export interface LoggerOptions {
  level: string;
  format: string;
}

/**
 * JSON logs by default (`LOG_FORMAT=text` switches to pino-pretty). Every line carries the
 * service name plus the active request ID and trace/span IDs so logs join to traces.
 */
export function createLogger(
  options: LoggerOptions,
  destination?: DestinationStream,
): Logger {
  const pretty = options.format === 'text' && destination === undefined;
  return pino(
    {
      level: options.level,
      base: { service: 'collab-service' },
      timestamp: pino.stdTimeFunctions.isoTime,
      transport: pretty
        ? { target: 'pino-pretty', options: { colorize: true } }
        : undefined,
      mixin() {
        const fields: Record<string, string> = {};
        const requestId = currentRequestId();
        if (requestId) {
          fields.requestId = requestId;
        }
        const spanContext = trace.getActiveSpan()?.spanContext();
        if (spanContext) {
          fields.traceId = spanContext.traceId;
          fields.spanId = spanContext.spanId;
        }
        return fields;
      },
    },
    destination,
  );
}
