import type { IncomingMessage } from 'http';
import { NodeSDK } from '@opentelemetry/sdk-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { HttpInstrumentation } from '@opentelemetry/instrumentation-http';
import type { Config } from '../config';

const UNTRACED_PATHS = new Set(['/health', '/metrics']);

function isUntraced(req: IncomingMessage): boolean {
  const path = (req.url ?? '').split('?')[0];
  return UNTRACED_PATHS.has(path);
}

/**
 * Starts the OpenTelemetry SDK. Must run before `http`/`express` are loaded so the HTTP
 * instrumentation can patch them; `src/instrumentation.ts` is imported first for this.
 * The exporter honours OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_EXPORTER_OTLP_TRACES_ENDPOINT.
 */
export function startTracing(otel: Config['otel']): NodeSDK | undefined {
  if (!otel.enabled) {
    return undefined;
  }
  const sdk = new NodeSDK({
    serviceName: otel.serviceName,
    traceExporter: new OTLPTraceExporter(),
    instrumentations: [
      new HttpInstrumentation({ ignoreIncomingRequestHook: isUntraced }),
    ],
  });
  sdk.start();
  return sdk;
}
