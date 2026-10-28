require 'rails_helper'

RSpec.describe JsonLogFormatter do
  subject(:formatter) { described_class.new }

  let(:time) { Time.utc(2026, 1, 1) }

  def render_line(msg)
    JSON.parse(formatter.call('INFO', time, nil, msg))
  end

  it 'emits one JSON object with service and message' do
    line = render_line('hello')
    expect(line).to include('level' => 'INFO', 'service' => 'admin-service', 'message' => 'hello')
  end

  it 'merges JSON messages such as lograge lines instead of nesting them' do
    line = render_line('{"method":"GET","status":200}')
    expect(line).to include('method' => 'GET', 'status' => 200)
    expect(line).not_to have_key('message')
  end

  it 'strips tagged-logging prefixes' do
    expect(render_line('[abc] [def] hi')['message']).to eq('hi')
  end

  it 'includes request, trace and span IDs when present' do
    ActiveSupport::IsolatedExecutionState[:admin_request_id] = 'req-log'
    OpenTelemetry.tracer_provider.tracer('spec').in_span('work') do |span|
      line = render_line('inside')
      expect(line).to include('request_id' => 'req-log', 'trace_id' => span.context.hex_trace_id,
                              'span_id' => span.context.hex_span_id)
    end
  ensure
    ActiveSupport::IsolatedExecutionState.delete(:admin_request_id)
  end
end
