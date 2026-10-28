require_relative 'boot'

require 'rails'
require 'active_model/railtie'
require 'active_job/railtie'
require 'active_record/railtie'
require 'action_controller/railtie'
require 'action_mailer/railtie'
require 'rails/test_unit/railtie'

Bundler.require(*Rails.groups)

# Loaded before boot so its before_initialize hook can insert the OTel Rack middleware.
require 'opentelemetry/instrumentation/action_pack'
require 'opentelemetry/instrumentation/action_pack/railtie'

require_relative '../app/middleware/jwt_authenticator'
require_relative '../app/middleware/request_context'
require_relative '../lib/json_log_formatter'

module AdminService
  class Application < Rails::Application
    config.load_defaults 7.1
    config.api_only = true

    # Exclude middleware from Zeitwerk autoloading — it is manually
    # required via require_relative above, and Zeitwerk's inflection
    # rules would expect JWTAuthenticator instead of JwtAuthenticator.
    config.autoload_paths -= [root.join('app/middleware').to_s]
    config.eager_load_paths -= [root.join('app/middleware').to_s]
    config.active_job.queue_adapter = :sidekiq
    config.log_formatter = ENV['LOG_FORMAT'] == 'text' ? ::Logger::Formatter.new : JsonLogFormatter.new
    config.time_zone = 'UTC'

    config.middleware.insert_after ActionDispatch::RequestId, RequestContext
    config.middleware.use Rack::Attack
    config.middleware.use JwtAuthenticator

    # Structured JSON logging
    config.lograge.enabled = true
    config.lograge.formatter = Lograge::Formatters::Json.new
    config.lograge.custom_payload do |controller|
      span_context = OpenTelemetry::Trace.current_span.context
      {
        service: 'admin-service',
        host: Socket.gethostname,
        request_id: controller.request.request_id,
        trace_id: span_context.valid? ? span_context.hex_trace_id : nil,
        span_id: span_context.valid? ? span_context.hex_span_id : nil
      }.compact
    end
  end
end
