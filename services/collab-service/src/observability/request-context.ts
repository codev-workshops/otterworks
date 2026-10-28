import { AsyncLocalStorage } from 'async_hooks';
import { randomUUID } from 'crypto';
import type { ErrorRequestHandler, Request, RequestHandler } from 'express';
import { trace } from '@opentelemetry/api';
import type { Logger } from 'pino';
import type { MetricsCollector } from '../metrics';

export const REQUEST_ID_HEADER = 'X-Request-ID';

const VALID_REQUEST_ID = /^[A-Za-z0-9._:-]{1,128}$/;
const PROBE_PATHS = new Set(['/health', '/metrics']);
const requestIdStorage = new AsyncLocalStorage<string>();

export function resolveRequestId(candidate: unknown): string {
  return typeof candidate === 'string' && VALID_REQUEST_ID.test(candidate)
    ? candidate
    : randomUUID();
}

export function currentRequestId(): string | undefined {
  return requestIdStorage.getStore();
}

function routeLabel(req: Request): string {
  const routePath: unknown = req.route?.path;
  return typeof routePath === 'string' ? `${req.baseUrl}${routePath}` : 'unmatched';
}

/**
 * Reuses a valid inbound X-Request-ID (or generates one), echoes it, tags the active span,
 * keeps it in async context for log lines, and records an access log plus HTTP metrics.
 */
export function createRequestContextMiddleware(
  logger: Logger,
  metrics: MetricsCollector,
): RequestHandler {
  return (req, res, next) => {
    const requestId = resolveRequestId(req.get(REQUEST_ID_HEADER));
    res.locals.requestId = requestId;
    res.setHeader(REQUEST_ID_HEADER, requestId);

    const span = trace.getActiveSpan();
    span?.setAttribute('request.id', requestId);
    const spanContext = span?.spanContext();
    const start = process.hrtime.bigint();

    res.on('finish', () => {
      const seconds = Number(process.hrtime.bigint() - start) / 1e9;
      const route = routeLabel(req);
      const status = res.statusCode;
      metrics.httpRequestsTotal.inc({
        method: req.method,
        route,
        status: String(status),
      });
      metrics.httpRequestDuration.observe({ method: req.method, route }, seconds);

      const level = PROBE_PATHS.has(req.path) ? 'debug' : 'info';
      logger[level](
        {
          requestId,
          traceId: spanContext?.traceId,
          spanId: spanContext?.spanId,
          method: req.method,
          path: req.path,
          route,
          status,
          durationMs: Math.round(seconds * 100000) / 100,
        },
        'http_request',
      );
    });

    requestIdStorage.run(requestId, next);
  };
}

/**
 * Returns a JSON 500 carrying the request ID instead of Express's HTML stack page.
 * Client errors (e.g. malformed JSON bodies) keep Express's default handling.
 */
export function createErrorHandler(logger: Logger): ErrorRequestHandler {
  return (err, req, res, next) => {
    const status = err?.status ?? err?.statusCode;
    if (res.headersSent || (typeof status === 'number' && status < 500)) {
      next(err);
      return;
    }
    const requestId: string = res.locals.requestId ?? resolveRequestId(undefined);
    logger.error(
      { err, requestId, method: req.method, path: req.path },
      'unhandled_request_error',
    );
    res.status(500).json({ error: 'Internal server error', requestId });
  };
}
