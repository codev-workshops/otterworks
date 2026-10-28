import express from 'express';
import { createServer, type Server } from 'http';
import type { AddressInfo } from 'net';
import { Writable } from 'stream';
import type { Socket } from 'socket.io';
import { context, trace } from '@opentelemetry/api';
import { AsyncLocalStorageContextManager } from '@opentelemetry/context-async-hooks';
import {
  BasicTracerProvider,
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { MetricsCollector } from '../metrics';
import { createAuthMiddleware } from '../middleware/auth';
import { createLogger } from '../observability/logger';
import {
  REQUEST_ID_HEADER,
  createErrorHandler,
  createRequestContextMiddleware,
} from '../observability/request-context';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const exporter = new InMemorySpanExporter();
const provider = new BasicTracerProvider();
provider.addSpanProcessor(new SimpleSpanProcessor(exporter));
const contextManager = new AsyncLocalStorageContextManager().enable();
context.setGlobalContextManager(contextManager);
trace.setGlobalTracerProvider(provider);
const tracer = trace.getTracer('collab-service-test');

interface Harness {
  baseUrl: string;
  logs: Record<string, unknown>[];
  metrics: MetricsCollector;
  server: Server;
}

async function startHarness(): Promise<Harness> {
  const logs: Record<string, unknown>[] = [];
  const sink = new Writable({
    write(chunk, _encoding, callback) {
      logs.push(JSON.parse(chunk.toString()));
      callback();
    },
  });
  const logger = createLogger({ level: 'debug', format: 'json' }, sink);
  const metrics = new MetricsCollector();

  const app = express();
  app.use(createRequestContextMiddleware(logger, metrics));
  app.use(express.json());
  app.get('/health', (_req, res) => res.json({ status: 'healthy' }));
  app.get('/metrics', async (_req, res) => {
    res.type(metrics.getContentType()).send(await metrics.getMetrics());
  });
  app.get('/api/v1/collab/documents/:id/presence', async (req, res) => {
    await new Promise((resolve) => setImmediate(resolve));
    logger.info({ documentId: req.params.id }, 'presence_lookup');
    res.json({ users: [] });
  });
  app.post('/echo', (req, res) => res.json(req.body));
  app.get('/boom', () => {
    throw new Error('redis password=hunter2 leaked');
  });
  app.use(createErrorHandler(logger));

  // Stands in for the HTTP auto-instrumentation: one active server span per request.
  const server = createServer((req, res) => {
    tracer.startActiveSpan(`${req.method} ${req.url}`, (span) => {
      res.on('finish', () => span.end());
      app(req, res);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return { baseUrl: `http://127.0.0.1:${port}`, logs, metrics, server };
}

function accessLog(h: Harness, path: string): Record<string, unknown> {
  const entry = h.logs.find((l) => l.msg === 'http_request' && l.path === path);
  if (!entry) {
    throw new Error(`no access log for ${path}`);
  }
  return entry;
}

async function waitForAccessLog(
  h: Harness,
  path: string,
): Promise<Record<string, unknown>> {
  for (let i = 0; i < 50; i += 1) {
    if (h.logs.some((l) => l.msg === 'http_request' && l.path === path)) {
      return accessLog(h, path);
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return accessLog(h, path);
}

describe('HTTP observability', () => {
  let h: Harness;

  beforeEach(async () => {
    exporter.reset();
    h = await startHarness();
  });

  afterEach(async () => {
    await new Promise((resolve) => h.server.close(resolve));
  });

  afterAll(() => {
    contextManager.disable();
  });

  it('echoes a valid inbound request ID', async () => {
    const res = await fetch(`${h.baseUrl}/health`, {
      headers: { [REQUEST_ID_HEADER]: 'abc-123.x:1' },
    });
    expect(res.headers.get(REQUEST_ID_HEADER)).toBe('abc-123.x:1');
  });

  it('generates a UUID when the request ID is missing', async () => {
    const res = await fetch(`${h.baseUrl}/health`);
    expect(res.headers.get(REQUEST_ID_HEADER)).toMatch(UUID);
  });

  it('replaces an invalid request ID', async () => {
    const res = await fetch(`${h.baseUrl}/health`, {
      headers: { [REQUEST_ID_HEADER]: 'bad id <script>' },
    });
    expect(res.headers.get(REQUEST_ID_HEADER)).toMatch(UUID);
  });

  it('writes JSON access logs joined to the request and trace', async () => {
    const res = await fetch(`${h.baseUrl}/api/v1/collab/documents/doc-1/presence`, {
      headers: { [REQUEST_ID_HEADER]: 'req-42' },
    });
    expect(res.status).toBe(200);
    const entry = await waitForAccessLog(h, '/api/v1/collab/documents/doc-1/presence');

    const [span] = exporter.getFinishedSpans();
    expect(span.attributes['request.id']).toBe('req-42');
    expect(entry).toMatchObject({
      service: 'collab-service',
      level: 30,
      requestId: 'req-42',
      traceId: span.spanContext().traceId,
      method: 'GET',
      route: '/api/v1/collab/documents/:id/presence',
      status: 200,
    });
    expect(typeof entry.durationMs).toBe('number');

    const handlerLog = h.logs.find((l) => l.msg === 'presence_lookup');
    expect(handlerLog).toMatchObject({
      requestId: 'req-42',
      traceId: span.spanContext().traceId,
      documentId: 'doc-1',
    });
  });

  it('logs probe requests at debug level', async () => {
    await fetch(`${h.baseUrl}/health`);
    expect((await waitForAccessLog(h, '/health')).level).toBe(20);
  });

  it('records HTTP metrics by route template in Prometheus text format', async () => {
    await fetch(`${h.baseUrl}/api/v1/collab/documents/doc-1/presence`);
    await fetch(`${h.baseUrl}/api/v1/collab/documents/doc-2/presence`);
    await waitForAccessLog(h, '/api/v1/collab/documents/doc-2/presence');

    const res = await fetch(`${h.baseUrl}/metrics`);
    expect(res.headers.get('content-type')).toContain('text/plain');
    const body = await res.text();
    expect(body).toContain(
      'http_requests_total{method="GET",route="/api/v1/collab/documents/:id/presence",status="200"} 2',
    );
    expect(body).toContain('# TYPE http_request_duration_seconds histogram');
    expect(body).toContain('# TYPE collab_active_connections gauge');
  });

  it('returns a generic JSON 500 carrying the request ID', async () => {
    const res = await fetch(`${h.baseUrl}/boom`, {
      headers: { [REQUEST_ID_HEADER]: 'req-500' },
    });
    expect(res.status).toBe(500);
    const text = await res.text();
    expect(JSON.parse(text)).toEqual({
      error: 'Internal server error',
      requestId: 'req-500',
    });
    expect(text).not.toContain('hunter2');
    expect(h.logs.find((l) => l.msg === 'unhandled_request_error')).toMatchObject({
      requestId: 'req-500',
      level: 50,
    });
  });

  it('keeps the default 400 for malformed JSON bodies', async () => {
    const res = await fetch(`${h.baseUrl}/echo`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{not json',
    });
    expect(res.status).toBe(400);
    expect(h.logs.some((l) => l.msg === 'unhandled_request_error')).toBe(false);
  });
});

describe('socket auth metrics', () => {
  function fakeSocket(token?: string): Socket {
    return {
      id: 'sock-1',
      handshake: { auth: token ? { token } : {}, headers: {} },
    } as unknown as Socket;
  }

  it('counts rejected handshakes by reason', async () => {
    const metrics = new MetricsCollector();
    const logger = createLogger({ level: 'silent', format: 'json' });
    const middleware = createAuthMiddleware('secret', logger, metrics);

    middleware(fakeSocket(), () => undefined);
    middleware(fakeSocket('not-a-jwt'), () => undefined);

    const body = await metrics.getMetrics();
    expect(body).toContain(
      'collab_connection_errors_total{reason="auth_missing_token"} 1',
    );
    expect(body).toContain(
      'collab_connection_errors_total{reason="auth_invalid_token"} 1',
    );
  });
});
