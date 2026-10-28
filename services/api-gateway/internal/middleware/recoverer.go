package middleware

import (
	"encoding/json"
	"net/http"

	"github.com/rs/zerolog"
)

// Recoverer turns a handler panic into a generic JSON 500 that carries the
// request ID but no panic detail. http.ErrAbortHandler is re-raised so the
// server can abort the connection as usual.
func Recoverer(logger zerolog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				rec := recover()
				if rec == nil {
					return
				}
				if rec == http.ErrAbortHandler { //nolint:errorlint // sentinel compared by identity, as net/http does
					panic(rec)
				}
				requestID := GetRequestID(r.Context())
				logger.Error().
					Interface("panic", rec).
					Str("request_id", requestID).
					Str("method", r.Method).
					Str("path", r.URL.Path).
					Msg("unhandled panic")
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusInternalServerError)
				_ = json.NewEncoder(w).Encode(map[string]string{
					"error":      "internal server error",
					"request_id": requestID,
				})
			}()
			next.ServeHTTP(w, r)
		})
	}
}
