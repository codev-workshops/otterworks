require 'rails_helper'

RSpec.describe AuditLogger do
  def counter_line(outcome)
    ObservabilityMetrics.to_prometheus_text.lines.find do |l|
      l.include?('admin_audit_events_total{action="spec.action",resource_type="Spec"') &&
        l.include?("outcome=\"#{outcome}\"")
    end
  end

  it 'counts recorded audit events' do
    allow(AuditLog).to receive(:record!).and_return(true)
    described_class.log(action: 'spec.action', resource_type: 'Spec', actor_id: 'user-1')
    expect(counter_line('recorded')).to be_present
  end

  it 'counts audit events that fail to persist' do
    allow(AuditLog).to receive(:record!).and_raise(StandardError, 'db down')
    described_class.log(action: 'spec.action', resource_type: 'Spec')
    expect(counter_line('failed')).to be_present
  end

  it 'hashes actor IDs rather than logging them' do
    expect(described_class.hash_actor('user-1')).to match(/\A[0-9a-f]{16}\z/)
    expect(described_class.hash_actor(nil)).to be_nil
  end
end
