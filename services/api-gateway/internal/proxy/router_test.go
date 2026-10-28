package proxy

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/rs/zerolog"
	"github.com/stretchr/testify/assert"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"

	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/middleware"
)

func newTestRouter(target string) http.Handler {
	return middleware.RequestID(NewRouter(RouterConfig{
		Routes:        []Route{{Prefix: "/api/v1/documents", TargetURL: target}},
		CBManager:     NewCircuitBreakerManager(CircuitBreakerConfig{MaxRequests: 1, FailureRatio: 1}),
		Logger:        zerolog.Nop(),
		EnableTracing: true,
	}))
}

func TestProxy_ForwardsRequestIDAndTraceparent(t *testing.T) {
	otel.SetTextMapPropagator(propagation.TraceContext{})
	tracer := sdktrace.NewTracerProvider().Tracer("test")

	var gotRequestID, gotTraceparent string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotRequestID = r.Header.Get("X-Request-ID")
		gotTraceparent = r.Header.Get("traceparent")
	}))
	defer backend.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/v1/documents/1", nil)
	req.Header.Set("X-Request-ID", "req-fwd")
	ctx, span := tracer.Start(req.Context(), "server")
	defer span.End()
	rec := httptest.NewRecorder()
	newTestRouter(backend.URL).ServeHTTP(rec, req.WithContext(ctx))

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "req-fwd", gotRequestID)
	assert.Contains(t, gotTraceparent, span.SpanContext().TraceID().String())
}

func TestProxy_CountsUpstreamErrors(t *testing.T) {
	before := testutil.ToFloat64(upstreamErrorsTotal.WithLabelValues("/api/v1/documents", "proxy_error"))

	rec := httptest.NewRecorder()
	newTestRouter("http://127.0.0.1:1").ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/documents/1", nil))

	assert.Equal(t, http.StatusBadGateway, rec.Code)
	assert.Equal(t, before+1, testutil.ToFloat64(upstreamErrorsTotal.WithLabelValues("/api/v1/documents", "proxy_error")))
}
