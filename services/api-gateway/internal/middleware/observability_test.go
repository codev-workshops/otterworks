package middleware

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/rs/zerolog"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel/attribute"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

func TestRequestID_ReplacesMalformedAndForwardsUpstream(t *testing.T) {
	var upstreamHeader string
	handler := RequestID(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamHeader = r.Header.Get(RequestIDHeader)
	}))

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set(RequestIDHeader, "bad id <script>")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	echoed := rec.Header().Get(RequestIDHeader)
	assert.NotEqual(t, "bad id <script>", echoed)
	assert.Len(t, echoed, 36)
	assert.Equal(t, echoed, upstreamHeader, "generated ID must be forwarded to the backend")
}

func TestRequestID_TagsActiveSpan(t *testing.T) {
	recorder := tracetest.NewSpanRecorder()
	tracer := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder)).Tracer("test")

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set(RequestIDHeader, "req-1.a:b")
	ctx, span := tracer.Start(req.Context(), "server")
	RequestID(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {})).
		ServeHTTP(httptest.NewRecorder(), req.WithContext(ctx))
	span.End()

	ended := recorder.Ended()
	require.Len(t, ended, 1)
	assert.Contains(t, ended[0].Attributes(), attribute.String("request.id", "req-1.a:b"))
}

func TestLogger_IncludesTraceAndSpanIDs(t *testing.T) {
	var buf bytes.Buffer
	tracer := sdktrace.NewTracerProvider().Tracer("test")
	req := httptest.NewRequest(http.MethodGet, "/api/v1/documents/42", nil)
	ctx, span := tracer.Start(req.Context(), "server")
	defer span.End()

	h := RequestID(Logger(zerolog.New(&buf))(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {})))
	h.ServeHTTP(httptest.NewRecorder(), req.WithContext(ctx))

	var line map[string]any
	require.NoError(t, json.Unmarshal(buf.Bytes(), &line))
	sc := trace.SpanContextFromContext(ctx)
	assert.Equal(t, sc.TraceID().String(), line["trace_id"])
	assert.Equal(t, sc.SpanID().String(), line["span_id"])
	assert.NotEmpty(t, line["request_id"])
}

func TestRecoverer_ReturnsJSON500WithoutPanicDetail(t *testing.T) {
	var buf bytes.Buffer
	h := RequestID(Recoverer(zerolog.New(&buf))(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic("db password=hunter2")
	})))

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set(RequestIDHeader, "req-500")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusInternalServerError, rec.Code)
	assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))
	assert.JSONEq(t, `{"error":"internal server error","request_id":"req-500"}`, rec.Body.String())
	assert.NotContains(t, rec.Body.String(), "hunter2")
}

func TestRecoverer_ReraisesErrAbortHandler(t *testing.T) {
	h := Recoverer(zerolog.Nop())(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic(http.ErrAbortHandler)
	}))
	assert.PanicsWithValue(t, http.ErrAbortHandler, func() {
		h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/", nil))
	})
}
