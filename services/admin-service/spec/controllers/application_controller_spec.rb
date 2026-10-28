require 'rails_helper'

RSpec.describe ApplicationController do
  controller do
    def index
      raise 'bucket otterworks-prod secret detail'
    end
  end

  it 'returns a generic JSON 500 with the request ID and counts the error' do
    get :index
    expect(response).to have_http_status(:internal_server_error)
    body = response.parsed_body
    expect(body['error']).to eq('Internal server error')
    expect(body['request_id']).to eq(request.request_id)
    expect(response.body).not_to include('secret')
    expect(ObservabilityMetrics.to_prometheus_text)
      .to include('admin_unhandled_errors_total{error_class="RuntimeError"}')
  end
end
