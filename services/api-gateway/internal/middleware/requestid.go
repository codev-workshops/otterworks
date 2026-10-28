package middleware

import (
	"context"
	"net/http"
	"regexp"

	"github.com/google/uuid"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

type contextKey string

const requestIDKey contextKey = "request_id"

// RequestIDHeader is the header used to correlate a request across services.
const RequestIDHeader = "X-Request-ID"

var validRequestID = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,128}$`)

// RequestID reuses a well-formed inbound X-Request-ID or mints a UUID. The ID is
// stored in the request context, echoed in the response, set on the inbound
// request header (so the reverse proxy forwards it upstream) and recorded on the
// active span as request.id.
func RequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get(RequestIDHeader)
		if !validRequestID.MatchString(id) {
			id = uuid.New().String()
		}

		r.Header.Set(RequestIDHeader, id)
		trace.SpanFromContext(r.Context()).SetAttributes(attribute.String("request.id", id))
		ctx := context.WithValue(r.Context(), requestIDKey, id)
		w.Header().Set(RequestIDHeader, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// GetRequestID retrieves the request ID from the context.
func GetRequestID(ctx context.Context) string {
	if id, ok := ctx.Value(requestIDKey).(string); ok {
		return id
	}
	return ""
}
