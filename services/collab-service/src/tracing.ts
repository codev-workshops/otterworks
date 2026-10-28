// Imported first by index.ts so the http module is patched before it is loaded.
// Off unless OTEL_SDK_DISABLED=false; exports OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT.
import { NodeSDK } from '@opentelemetry/sdk-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { HttpInstrumentation } from '@opentelemetry/instrumentation-http';
import { Resource } from '@opentelemetry/resources';
import { SEMRESATTRS_SERVICE_NAME } from '@opentelemetry/semantic-conventions';

export function tracingEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return (env.OTEL_SDK_DISABLED ?? 'true').trim().toLowerCase() === 'false';
}

const IGNORED_PATHS = new Set(['/health', '/metrics']);

export function startTracing(env: NodeJS.ProcessEnv = process.env): NodeSDK | undefined {
  if (!tracingEnabled(env)) {
    return undefined;
  }
  const sdk = new NodeSDK({
    resource: new Resource({
      [SEMRESATTRS_SERVICE_NAME]: env.OTEL_SERVICE_NAME ?? 'collab-service',
    }),
    traceExporter: new OTLPTraceExporter(),
    instrumentations: [
      new HttpInstrumentation({
        ignoreIncomingRequestHook: (req) =>
          IGNORED_PATHS.has((req.url ?? '').split('?')[0]),
      }),
    ],
  });
  sdk.start();
  const shutdown = (): void => {
    sdk.shutdown().catch(() => undefined);
  };
  process.once('SIGTERM', shutdown);
  process.once('SIGINT', shutdown);
  return sdk;
}

startTracing();
