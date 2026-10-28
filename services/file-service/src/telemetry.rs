//! OpenTelemetry tracing: exports spans over OTLP/gRPC (`OTEL_EXPORTER_OTLP_ENDPOINT`).
//! Off unless `OTEL_SDK_DISABLED=false`.

use opentelemetry::KeyValue;
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_sdk::{propagation::TraceContextPropagator, runtime, trace, Resource};
use tracing::Subscriber;
use tracing_opentelemetry::OpenTelemetryLayer;
use tracing_subscriber::registry::LookupSpan;

pub fn tracing_enabled(value: Option<&str>) -> bool {
    value.is_some_and(|v| v.trim().eq_ignore_ascii_case("false"))
}

fn enabled_from_env() -> bool {
    tracing_enabled(std::env::var("OTEL_SDK_DISABLED").ok().as_deref())
}

/// Builds the OpenTelemetry layer and installs the W3C trace-context propagator, so
/// `TracingLogger` continues the caller's trace. Returns `None` when tracing is off.
pub fn otel_layer<S>() -> Option<OpenTelemetryLayer<S, trace::Tracer>>
where
    S: Subscriber + for<'span> LookupSpan<'span>,
{
    if !enabled_from_env() {
        return None;
    }
    opentelemetry::global::set_text_map_propagator(TraceContextPropagator::new());
    let service = std::env::var("OTEL_SERVICE_NAME").unwrap_or_else(|_| "file-service".into());
    let endpoint = std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT")
        .unwrap_or_else(|_| "http://localhost:4317".into());
    let tracer = opentelemetry_otlp::new_pipeline()
        .tracing()
        .with_exporter(
            opentelemetry_otlp::new_exporter()
                .tonic()
                .with_endpoint(endpoint),
        )
        .with_trace_config(
            trace::config()
                .with_resource(Resource::new(vec![KeyValue::new("service.name", service)])),
        )
        .install_batch(runtime::Tokio);
    match tracer {
        Ok(tracer) => Some(tracing_opentelemetry::layer().with_tracer(tracer)),
        Err(err) => {
            eprintln!("OpenTelemetry tracing disabled: {err}");
            None
        }
    }
}

/// Flushes buffered spans on shutdown.
pub fn shutdown() {
    if enabled_from_env() {
        opentelemetry::global::shutdown_tracer_provider();
    }
}

#[cfg(test)]
mod tests {
    use super::tracing_enabled;

    #[test]
    fn tracing_is_off_unless_explicitly_enabled() {
        assert!(!tracing_enabled(None));
        assert!(!tracing_enabled(Some("true")));
        assert!(!tracing_enabled(Some("")));
        assert!(tracing_enabled(Some("false")));
        assert!(tracing_enabled(Some(" FALSE ")));
    }
}
