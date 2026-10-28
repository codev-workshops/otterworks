# Runs after ActionDispatch::RequestId: exposes the resolved request ID to log
# lines and tags the active server span so logs and traces can be joined.
class RequestContext
  def initialize(app)
    @app = app
  end

  def call(env)
    request_id = env['action_dispatch.request_id']
    ActiveSupport::IsolatedExecutionState[:admin_request_id] = request_id
    OpenTelemetry::Trace.current_span.set_attribute('request.id', request_id) if request_id
    @app.call(env)
  ensure
    ActiveSupport::IsolatedExecutionState.delete(:admin_request_id)
  end
end
