class AuditLogger
  def self.log(action:, resource_type:, resource_id: nil, request: nil, actor_id: nil, actor_email: nil,
               changes_made: {})
    actor_id ||= request&.env&.dig('jwt.user_id')
    actor_email ||= request&.env&.dig('jwt.user_email')

    entry = AuditLog.record!(
      action: action,
      resource_type: resource_type,
      resource_id: resource_id,
      actor_id: actor_id,
      actor_email: actor_email,
      changes_made: changes_made,
      ip_address: request&.remote_ip,
      user_agent: request&.user_agent
    )
    ObservabilityMetrics.record_audit_event(action: action, resource_type: resource_type, outcome: 'recorded')
    Rails.logger.info({ event: 'audit_event', action: action, resource_type: resource_type,
                        resource_id: resource_id, actor_id_hash: hash_actor(actor_id) }.compact.to_json)
    entry
  rescue StandardError => e
    ObservabilityMetrics.record_audit_event(action: action, resource_type: resource_type, outcome: 'failed')
    Rails.logger.error("Failed to record audit log: #{e.message}")
  end

  def self.hash_actor(actor_id)
    return nil if actor_id.blank?

    Digest::SHA256.hexdigest(actor_id.to_s)[0, 16]
  end
end
