require 'rails_helper'

RSpec.describe 'Observability' do
  let(:exporter) { OpenTelemetry::SDK::Trace::Export::InMemorySpanExporter.new }
  let(:traceparent) { '00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01' }

  before do
    OpenTelemetry.tracer_provider.add_span_processor(
      OpenTelemetry::SDK::Trace::Export::SimpleSpanProcessor.new(exporter)
    )
  end

  it 'echoes the inbound request ID' do
    get '/health', headers: { 'X-Request-ID' => 'req-123' }
    expect(response.headers['X-Request-ID']).to eq('req-123')
  end

  it 'generates a request ID when none is sent' do
    get '/health'
    expect(response.headers['X-Request-ID']).to match(/\A[0-9a-f-]{36}\z/)
  end

  it 'continues the inbound trace and tags the server span with the request ID' do
    get '/health', headers: { 'traceparent' => traceparent, 'X-Request-ID' => 'req-trace' }
    server_span = exporter.finished_spans.find { |s| s.kind == :server }
    expect(server_span.hex_trace_id).to eq('0af7651916cd43dd8448eb211c80319c')
    expect(server_span.attributes['request.id']).to eq('req-trace')
  end

  it 'records HTTP request metrics by controller action' do
    get '/health'
    get '/metrics'
    expect(response.body).to include('admin_http_requests_total{controller="HealthController",action="show"')
    expect(response.body).to include('admin_http_request_duration_seconds_bucket')
  end

  it 'returns 503 when the database is unreachable' do
    allow(ActiveRecord::Base).to receive(:connection).and_raise(ActiveRecord::ConnectionNotEstablished)
    get '/health'
    expect(response).to have_http_status(:service_unavailable)
    expect(response.parsed_body['status']).to eq('degraded')
  end
end
