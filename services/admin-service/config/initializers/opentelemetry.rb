require 'opentelemetry/sdk'
require 'opentelemetry/instrumentation/rails'

unless ENV['TRACING_ENABLED'] == 'false'
  # Spans are exported over OTLP/HTTP only when a collector is configured; otherwise
  # trace and span IDs are still generated so logs stay correlated.
  otlp_endpoint = ENV['OTEL_EXPORTER_OTLP_TRACES_ENDPOINT'].presence || ENV['OTEL_EXPORTER_OTLP_ENDPOINT'].presence
  if otlp_endpoint && !Rails.env.test?
    require 'opentelemetry-exporter-otlp'
  else
    ENV['OTEL_TRACES_EXPORTER'] ||= 'none'
  end

  OpenTelemetry::SDK.configure do |c|
    c.service_name = ENV.fetch('OTEL_SERVICE_NAME', 'admin-service')
    c.use 'OpenTelemetry::Instrumentation::Rails'
  end
end
