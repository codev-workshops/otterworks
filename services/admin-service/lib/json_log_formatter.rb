require 'json'
require 'logger'

# One JSON object per line, correlated with the active request and trace.
# Messages that are already JSON objects (lograge) are merged in rather than nested.
class JsonLogFormatter < Logger::Formatter
  def call(severity, time, _progname, msg)
    record = {
      timestamp: time.utc.iso8601(3),
      level: severity,
      service: 'admin-service'
    }
    record.merge!(correlation_fields)
    record.merge!(message_fields(msg))
    "#{JSON.generate(record)}\n"
  end

  private

  def correlation_fields
    fields = {}
    request_id = ActiveSupport::IsolatedExecutionState[:admin_request_id]
    fields[:request_id] = request_id if request_id
    context = OpenTelemetry::Trace.current_span.context
    if context.valid?
      fields[:trace_id] = context.hex_trace_id
      fields[:span_id] = context.hex_span_id
    end
    fields
  end

  def message_fields(msg)
    text = msg.is_a?(String) ? msg : msg.inspect
    text = text.sub(/\A(?:\[[^\]]*\] )+/, '')
    if text.start_with?('{')
      parsed = JSON.parse(text)
      return parsed.transform_keys(&:to_sym) if parsed.is_a?(Hash)
    end
    { message: text }
  rescue JSON::ParserError
    { message: text }
  end
end
