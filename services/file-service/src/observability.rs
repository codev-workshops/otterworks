//! Tracing, request-ID and log-correlation setup shared by the HTTP stack.

use actix_web::body::MessageBody;
use actix_web::dev::{ServiceRequest, ServiceResponse};
use actix_web::{Error, HttpMessage};
use opentelemetry::propagation::Extractor;
use opentelemetry::trace::TraceContextExt;
use opentelemetry::KeyValue;
use opentelemetry_sdk::propagation::TraceContextPropagator;
use opentelemetry_sdk::{trace as sdktrace, Resource};
use tracing::Span;
use tracing_actix_web::{DefaultRootSpanBuilder, RootSpanBuilder};
use tracing_opentelemetry::OpenTelemetrySpanExt;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};
use uuid::Uuid;

pub const REQUEST_ID_HEADER: &str = "x-request-id";
pub const SERVICE_NAME: &str = "file-service";

/// The request ID resolved for the current request, stored in request extensions.
#[derive(Clone, Debug)]
pub struct RequestId(pub String);

/// Reuse a well-formed inbound request ID (`[A-Za-z0-9._:-]{1,128}`), otherwise mint a UUID.
pub fn resolve_request_id(candidate: Option<&str>) -> String {
    match candidate {
        Some(id)
            if !id.is_empty()
                && id.len() <= 128
                && id.bytes().all(|b| {
                    b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b':' | b'-')
                }) =>
        {
            id.to_string()
        }
        _ => Uuid::new_v4().to_string(),
    }
}

/// Builds the tracer provider. Spans are exported over OTLP/HTTP only when an
/// `OTEL_EXPORTER_OTLP_(TRACES_)ENDPOINT` is configured and `TRACING_ENABLED` is not `false`;
/// otherwise trace IDs are still generated so logs stay correlatable.
pub fn tracer_provider() -> sdktrace::TracerProvider {
    let config = sdktrace::config().with_resource(Resource::new(vec![KeyValue::new(
        "service.name",
        std::env::var("OTEL_SERVICE_NAME").unwrap_or_else(|_| SERVICE_NAME.into()),
    )]));
    let mut builder = sdktrace::TracerProvider::builder().with_config(config);

    let enabled = !std::env::var("TRACING_ENABLED")
        .map(|v| v.eq_ignore_ascii_case("false"))
        .unwrap_or(false);
    let has_endpoint = std::env::var_os("OTEL_EXPORTER_OTLP_ENDPOINT").is_some()
        || std::env::var_os("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT").is_some();
    if enabled && has_endpoint {
        match opentelemetry_otlp::new_exporter()
            .http()
            .build_span_exporter()
        {
            Ok(exporter) => {
                builder = builder.with_batch_exporter(exporter, opentelemetry_sdk::runtime::Tokio)
            }
            Err(err) => {
                eprintln!("failed to build OTLP exporter, spans will not be exported: {err}")
            }
        }
    }
    builder.build()
}

/// Installs the global propagator and the JSON (default) or text (`LOG_FORMAT=text`) subscriber
/// with an OpenTelemetry layer.
pub fn init_telemetry() -> sdktrace::TracerProvider {
    use opentelemetry::trace::TracerProvider as _;

    opentelemetry::global::set_text_map_propagator(TraceContextPropagator::new());
    let provider = tracer_provider();
    let otel_layer = tracing_opentelemetry::layer().with_tracer(provider.tracer(SERVICE_NAME));
    let filter = tracing_subscriber::EnvFilter::new(
        std::env::var("RUST_LOG").unwrap_or_else(|_| "file_service=debug,actix_web=info".into()),
    );
    let registry = tracing_subscriber::registry().with(filter).with(otel_layer);

    if std::env::var("LOG_FORMAT").is_ok_and(|v| v.eq_ignore_ascii_case("text")) {
        registry.with(tracing_subscriber::fmt::layer()).init();
    } else {
        registry
            .with(
                tracing_subscriber::fmt::layer()
                    .json()
                    .with_current_span(true)
                    .with_span_list(false),
            )
            .init();
    }
    provider
}

/// Trace and span IDs of the current span, if it is being recorded by OpenTelemetry.
pub fn current_trace_ids() -> Option<(String, String)> {
    let context = Span::current().context();
    let span = context.span();
    let sc = span.span_context();
    sc.is_valid()
        .then(|| (sc.trace_id().to_string(), sc.span_id().to_string()))
}

struct HeaderExtractor<'a>(&'a actix_web::http::header::HeaderMap);

impl Extractor for HeaderExtractor<'_> {
    fn get(&self, key: &str) -> Option<&str> {
        self.0.get(key).and_then(|v| v.to_str().ok())
    }

    fn keys(&self) -> Vec<&str> {
        self.0.keys().map(|k| k.as_str()).collect()
    }
}

/// Root span for every request: continues an inbound W3C `traceparent`, uses the resolved
/// `X-Request-ID` as `request_id` (instead of tracing-actix-web's own UUID) and sets it as the
/// `request.id` span attribute.
pub struct FileServiceRootSpan;

impl RootSpanBuilder for FileServiceRootSpan {
    fn on_request_start(request: &ServiceRequest) -> Span {
        let request_id = resolve_request_id(
            request
                .headers()
                .get(REQUEST_ID_HEADER)
                .and_then(|v| v.to_str().ok()),
        );
        request
            .extensions_mut()
            .insert(RequestId(request_id.clone()));

        let route = request
            .match_pattern()
            .unwrap_or_else(|| "unmatched".to_string());
        let method = request.method().as_str();
        let span = tracing::info_span!(
            "HTTP request",
            otel.name = %format!("{method} {route}"),
            otel.kind = "server",
            http.method = %method,
            http.route = %route,
            request_id = %request_id,
            trace_id = tracing::field::Empty,
            http.status_code = tracing::field::Empty,
            otel.status_code = tracing::field::Empty,
            exception.message = tracing::field::Empty,
            exception.details = tracing::field::Empty,
        );

        let parent = opentelemetry::global::get_text_map_propagator(|p| {
            p.extract(&HeaderExtractor(request.headers()))
        });
        span.set_parent(parent);
        span.set_attribute("request.id", request_id);
        let trace_id = span.context().span().span_context().trace_id();
        if trace_id != opentelemetry::trace::TraceId::INVALID {
            span.record("trace_id", tracing::field::display(trace_id));
        }
        span
    }

    fn on_request_end<B: MessageBody>(span: Span, outcome: &Result<ServiceResponse<B>, Error>) {
        DefaultRootSpanBuilder::on_request_end(span, outcome);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reuses_well_formed_request_ids() {
        assert_eq!(resolve_request_id(Some("req-1.a:b_c")), "req-1.a:b_c");
    }

    #[test]
    fn replaces_missing_or_malformed_request_ids() {
        for candidate in [None, Some(""), Some("bad id"), Some("<script>")] {
            let id = resolve_request_id(candidate);
            assert!(Uuid::parse_str(&id).is_ok(), "{candidate:?} -> {id}");
        }
        assert!(Uuid::parse_str(&resolve_request_id(Some(&"a".repeat(129)))).is_ok());
    }
}
