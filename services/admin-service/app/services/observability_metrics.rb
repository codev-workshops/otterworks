require 'prometheus_exporter/metric'

# In-process Prometheus registry rendered by HealthController#metrics.
module ObservabilityMetrics
  DURATION_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10].freeze

  HTTP_REQUESTS = PrometheusExporter::Metric::Counter.new(
    'admin_http_requests_total', 'HTTP requests by controller action and status'
  )
  HTTP_DURATION = PrometheusExporter::Metric::Histogram.new(
    'admin_http_request_duration_seconds', 'HTTP request duration by controller action',
    buckets: DURATION_BUCKETS
  )
  AUDIT_EVENTS = PrometheusExporter::Metric::Counter.new(
    'admin_audit_events_total', 'Admin audit events by action, resource type and outcome'
  )
  UNHANDLED_ERRORS = PrometheusExporter::Metric::Counter.new(
    'admin_unhandled_errors_total', 'Unhandled exceptions by class'
  )

  MUTEX = Mutex.new

  module_function

  def record_request(controller:, action:, method:, status:, duration_seconds:)
    labels = { controller: controller, action: action }
    MUTEX.synchronize do
      HTTP_REQUESTS.observe(1, labels.merge(method: method, status: status.to_s))
      HTTP_DURATION.observe(duration_seconds, labels)
    end
  end

  def record_audit_event(action:, resource_type:, outcome:)
    MUTEX.synchronize do
      AUDIT_EVENTS.observe(1, action: action.to_s, resource_type: resource_type.to_s, outcome: outcome)
    end
  end

  def record_unhandled_error(error)
    MUTEX.synchronize { UNHANDLED_ERRORS.observe(1, error_class: error.class.name) }
  end

  def to_prometheus_text
    MUTEX.synchronize do
      [HTTP_REQUESTS, HTTP_DURATION, AUDIT_EVENTS, UNHANDLED_ERRORS].map(&:to_prometheus_text).join("\n")
    end
  end
end
