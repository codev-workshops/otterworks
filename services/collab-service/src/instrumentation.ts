import { loadConfig } from './config';
import { startTracing } from './observability/tracing';

export const tracingSdk = startTracing(loadConfig().otel);
