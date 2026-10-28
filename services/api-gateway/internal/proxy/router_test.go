package proxy

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/rs/zerolog"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"

	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/middleware"
)

func newTestRouter(t *testing.T, backendURL string, tracing bool) http.Handler {
	t.Helper()
	cb := NewCircuitBreakerManager(CircuitBreakerConfig{
		MaxRequests:  2,
		Interval:     60 * time.Second,
		Timeout:      10 * time.Second,
		FailureRatio: 0.5,
	})
	r := NewRouter(RouterConfig{
		Routes:        []Route{{Prefix: "/api/v1/docs", TargetURL: backendURL}},
		CBManager:     cb,
		Logger:        zerolog.Nop(),
		EnableTracing: tracing,
	})
	return middleware.RequestID(r)
}

func TestProxy_ForwardsGeneratedRequestID(t *testing.T) {
	var got string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Get("X-Request-ID")
	}))
	defer backend.Close()

	rec := httptest.NewRecorder()
	newTestRouter(t, backend.URL, false).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/docs/1", nil))

	require.NotEmpty(t, got)
	assert.Equal(t, rec.Header().Get("X-Request-ID"), got)
}

func TestProxy_InjectsTraceContextWhenTracingEnabled(t *testing.T) {
	prevTP, prevProp := otel.GetTracerProvider(), otel.GetTextMapPropagator()
	tp := sdktrace.NewTracerProvider()
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	t.Cleanup(func() {
		_ = tp.Shutdown(context.Background())
		otel.SetTracerProvider(prevTP)
		otel.SetTextMapPropagator(prevProp)
	})

	var traceparent string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		traceparent = r.Header.Get("traceparent")
	}))
	defer backend.Close()

	incoming := "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
	req := httptest.NewRequest(http.MethodGet, "/api/v1/docs/1", nil)
	req.Header.Set("traceparent", incoming)
	newTestRouter(t, backend.URL, true).ServeHTTP(httptest.NewRecorder(), req)

	require.NotEmpty(t, traceparent)
	assert.Contains(t, traceparent, "4bf92f3577b34da6a3ce929d0e0e4736", "backend must join the caller's trace")
	assert.NotEqual(t, incoming, traceparent, "backend must see the gateway's client span as parent")
}
