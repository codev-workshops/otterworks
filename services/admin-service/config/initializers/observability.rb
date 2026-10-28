ActiveSupport::Notifications.subscribe('process_action.action_controller') do |event|
  payload = event.payload
  status = payload[:status] || (payload[:exception] ? 500 : 0)
  ObservabilityMetrics.record_request(
    controller: payload[:controller],
    action: payload[:action],
    method: payload[:method],
    status: status,
    duration_seconds: event.duration / 1000.0
  )
end
