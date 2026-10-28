package com.otterworks.auth.observability;

import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;

/** Security-event counters, exported as {@code auth_*_total} on {@code /metrics}. */
@Component
public class AuthMetrics {

  public static final String SUCCESS = "success";

  private final MeterRegistry registry;

  public AuthMetrics(MeterRegistry registry) {
    this.registry = registry;
  }

  public void login(String outcome) {
    registry.counter("auth.logins", "outcome", outcome).increment();
  }

  public void registration(String outcome) {
    registry.counter("auth.registrations", "outcome", outcome).increment();
  }

  public void tokenRefresh(String outcome) {
    registry.counter("auth.token.refreshes", "outcome", outcome).increment();
  }

  public void passwordChange(String outcome) {
    registry.counter("auth.password.changes", "outcome", outcome).increment();
  }

  public void logout() {
    registry.counter("auth.logouts").increment();
  }

  public void rejectedToken(String reason) {
    registry.counter("auth.tokens.rejected", "reason", reason).increment();
  }
}
