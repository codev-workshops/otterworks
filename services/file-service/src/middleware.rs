use actix_web::dev::{Service, ServiceRequest, ServiceResponse, Transform};
use actix_web::http::header::{HeaderName, HeaderValue};
use actix_web::{Error, HttpMessage};
use futures_util::future::{ok, LocalBoxFuture, Ready};
use std::task::{Context, Poll};

use crate::observability::{self, REQUEST_ID_HEADER};

use lazy_static::lazy_static;
use prometheus::{
    register_histogram_vec, register_int_counter_vec, Encoder, HistogramVec, IntCounterVec,
    TextEncoder,
};

lazy_static! {
    pub static ref HTTP_REQUESTS_TOTAL: IntCounterVec = register_int_counter_vec!(
        "http_requests_total",
        "Total HTTP requests",
        &["method", "path", "status"]
    )
    .expect("metric can be created");
    pub static ref HTTP_REQUEST_DURATION: HistogramVec = register_histogram_vec!(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        &["method", "path"],
        vec![0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
    )
    .expect("metric can be created");
    pub static ref FILE_OPERATIONS_TOTAL: IntCounterVec = register_int_counter_vec!(
        "file_operations_total",
        "File and folder write/download operations by outcome",
        &["operation", "outcome"]
    )
    .expect("metric can be created");
    pub static ref FILE_UPLOAD_SIZE_BYTES: prometheus::Histogram = prometheus::register_histogram!(
        "file_upload_size_bytes",
        "Size of successfully uploaded files in bytes",
        prometheus::exponential_buckets(1024.0, 4.0, 10).expect("valid buckets")
    )
    .expect("metric can be created");
    pub static ref FILE_BACKEND_ERRORS_TOTAL: IntCounterVec = register_int_counter_vec!(
        "file_backend_errors_total",
        "Requests that failed because of a backing service",
        &["backend"]
    )
    .expect("metric can be created");
}

/// Domain operation for a matched route, or `None` for reads that are covered by HTTP metrics.
pub fn operation_for(method: &str, pattern: &str) -> Option<&'static str> {
    Some(match (method, pattern) {
        ("POST", "/api/v1/files/upload") => "upload",
        ("GET", "/api/v1/files/{file_id}/download") => "download",
        ("DELETE", "/api/v1/files/{file_id}") => "delete",
        ("PUT", "/api/v1/files/{file_id}/move") => "move",
        ("PATCH", "/api/v1/files/{file_id}/rename") => "rename",
        ("POST", "/api/v1/files/{file_id}/trash") => "trash",
        ("POST", "/api/v1/files/{file_id}/restore") => "restore",
        ("POST", "/api/v1/files/{file_id}/share") => "share",
        ("DELETE", "/api/v1/files/{file_id}/share/{user_id}") => "unshare",
        ("POST", "/api/v1/folders") => "create_folder",
        ("PUT", "/api/v1/folders/{folder_id}") => "update_folder",
        ("DELETE", "/api/v1/folders/{folder_id}") => "delete_folder",
        _ => return None,
    })
}

fn outcome_for(status: u16) -> &'static str {
    match status {
        0..=399 => "success",
        400..=499 => "client_error",
        _ => "server_error",
    }
}

pub fn render_metrics() -> String {
    let encoder = TextEncoder::new();
    let metric_families = prometheus::gather();
    let mut buffer = Vec::new();
    encoder.encode(&metric_families, &mut buffer).unwrap();
    String::from_utf8(buffer).unwrap()
}

// -- Request ID Middleware --
//
// Must run inside `TracingLogger<FileServiceRootSpan>`, which resolves the request ID.
// Echoes `X-Request-ID`, records HTTP and domain metrics and writes the access log line.

pub struct RequestId;

impl<S, B> Transform<S, ServiceRequest> for RequestId
where
    S: Service<ServiceRequest, Response = ServiceResponse<B>, Error = Error>,
    S::Future: 'static,
    B: 'static,
{
    type Response = ServiceResponse<B>;
    type Error = Error;
    type Transform = RequestIdMiddleware<S>;
    type InitError = ();
    type Future = Ready<Result<Self::Transform, Self::InitError>>;

    fn new_transform(&self, service: S) -> Self::Future {
        ok(RequestIdMiddleware { service })
    }
}

pub struct RequestIdMiddleware<S> {
    service: S,
}

impl<S, B> Service<ServiceRequest> for RequestIdMiddleware<S>
where
    S: Service<ServiceRequest, Response = ServiceResponse<B>, Error = Error>,
    S::Future: 'static,
    B: 'static,
{
    type Response = ServiceResponse<B>;
    type Error = Error;
    type Future = LocalBoxFuture<'static, Result<Self::Response, Self::Error>>;

    fn poll_ready(&self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.service.poll_ready(cx)
    }

    fn call(&self, req: ServiceRequest) -> Self::Future {
        let request_id = req
            .extensions()
            .get::<observability::RequestId>()
            .map(|r| r.0.clone())
            .unwrap_or_else(|| {
                observability::resolve_request_id(
                    req.headers()
                        .get(REQUEST_ID_HEADER)
                        .and_then(|v| v.to_str().ok()),
                )
            });

        let method = req.method().to_string();
        let path = req
            .match_pattern()
            .unwrap_or_else(|| "unmatched".to_string());
        let start = std::time::Instant::now();

        let fut = self.service.call(req);

        Box::pin(async move {
            let mut res = fut.await?;
            let elapsed = start.elapsed().as_secs_f64();
            let status_code = res.status().as_u16();
            let status = status_code.to_string();
            if let Ok(value) = HeaderValue::from_str(&request_id) {
                res.headers_mut()
                    .insert(HeaderName::from_static(REQUEST_ID_HEADER), value);
            }
            if let Some(operation) = operation_for(&method, &path) {
                FILE_OPERATIONS_TOTAL
                    .with_label_values(&[operation, outcome_for(status_code)])
                    .inc();
            }
            let (trace_id, span_id) = observability::current_trace_ids().unwrap_or_default();

            HTTP_REQUESTS_TOTAL
                .with_label_values(&[&method, &path, &status])
                .inc();
            HTTP_REQUEST_DURATION
                .with_label_values(&[&method, &path])
                .observe(elapsed);

            tracing::info!(
                request_id = %request_id,
                trace_id = %trace_id,
                span_id = %span_id,
                method = %method,
                path = %path,
                status = %status,
                duration_ms = %(elapsed * 1000.0),
                "Request completed"
            );

            Ok(res)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::errors::ServiceError;
    use crate::observability::FileServiceRootSpan;
    use actix_web::{test as atest, web, App, HttpResponse, ResponseError};
    use opentelemetry::trace::TracerProvider as _;
    use tracing_subscriber::layer::SubscriberExt;

    macro_rules! app {
        () => {
            atest::init_service(
                App::new()
                    .wrap(RequestId)
                    .wrap(tracing_actix_web::TracingLogger::<FileServiceRootSpan>::new())
                    .route(
                        "/trace",
                        web::get().to(|| async {
                            let (trace_id, _) =
                                crate::observability::current_trace_ids().unwrap_or_default();
                            HttpResponse::Ok().body(trace_id)
                        }),
                    )
                    .route(
                        "/api/v1/files/{file_id}",
                        web::delete().to(|| async {
                            Err::<HttpResponse, _>(ServiceError::FileNotFound("x".into()))
                        }),
                    ),
            )
            .await
        };
    }

    #[actix_rt::test]
    async fn echoes_valid_request_id_and_replaces_malformed_one() {
        let app = app!();
        let req = atest::TestRequest::get()
            .uri("/trace")
            .insert_header(("X-Request-ID", "req-123"))
            .to_request();
        let resp = atest::call_service(&app, req).await;
        assert_eq!(resp.headers().get("x-request-id").unwrap(), "req-123");

        let req = atest::TestRequest::get()
            .uri("/trace")
            .insert_header(("X-Request-ID", "bad id"))
            .to_request();
        let resp = atest::call_service(&app, req).await;
        let id = resp
            .headers()
            .get("x-request-id")
            .unwrap()
            .to_str()
            .unwrap();
        assert!(uuid::Uuid::parse_str(id).is_ok());
    }

    #[actix_rt::test]
    async fn continues_inbound_traceparent() {
        opentelemetry::global::set_text_map_propagator(
            opentelemetry_sdk::propagation::TraceContextPropagator::new(),
        );
        let provider = opentelemetry_sdk::trace::TracerProvider::builder().build();
        let subscriber = tracing_subscriber::registry()
            .with(tracing_opentelemetry::layer().with_tracer(provider.tracer("test")));
        let _guard = tracing::subscriber::set_default(subscriber);

        let app = app!();
        let req = atest::TestRequest::get()
            .uri("/trace")
            .insert_header((
                "traceparent",
                "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
            ))
            .to_request();
        let body = atest::call_and_read_body(&app, req).await;
        assert_eq!(body, "0af7651916cd43dd8448eb211c80319c");
    }

    #[actix_rt::test]
    async fn counts_domain_operations_by_outcome() {
        let counter = FILE_OPERATIONS_TOTAL.with_label_values(&["delete", "client_error"]);
        let before = counter.get();
        let app = app!();
        let req = atest::TestRequest::delete()
            .uri("/api/v1/files/abc")
            .to_request();
        let resp = atest::call_service(&app, req).await;
        assert_eq!(resp.status(), 404);
        assert_eq!(counter.get(), before + 1);
        assert!(render_metrics().contains("file_operations_total"));
    }

    #[test]
    fn maps_only_write_and_download_routes_to_operations() {
        assert_eq!(
            operation_for("POST", "/api/v1/files/upload"),
            Some("upload")
        );
        assert_eq!(
            operation_for("GET", "/api/v1/files/{file_id}/download"),
            Some("download")
        );
        assert_eq!(operation_for("GET", "/api/v1/files"), None);
    }

    #[actix_rt::test]
    async fn backend_errors_return_generic_message_and_are_counted() {
        let counter = FILE_BACKEND_ERRORS_TOTAL.with_label_values(&["s3"]);
        let before = counter.get();
        let resp =
            ServiceError::S3Error("NoSuchBucket: otterworks-prod-files".into()).error_response();
        assert_eq!(resp.status(), 500);
        let body = actix_web::body::to_bytes(resp.into_body()).await.unwrap();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(json["error"], "storage_error");
        assert_eq!(json["message"], "Internal server error");
        assert_eq!(counter.get(), before + 1);
    }
}
