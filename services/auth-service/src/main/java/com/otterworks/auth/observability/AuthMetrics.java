package com.otterworks.auth.observability;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;
import java.util.function.Supplier;
import org.springframework.stereotype.Component;

/**
 * Domain metrics for auth flows. Exported on {@code /metrics} with Prometheus names, e.g. {@code
 * auth_login_attempts_total{result,reason}}, {@code auth_token_issued_total{type,grant}} and {@code
 * auth_request_duration_seconds{operation,outcome}}.
 */
@Component
public class AuthMetrics {

  public static final String SUCCESS = "success";
  public static final String FAILURE = "failure";

  private final MeterRegistry registry;

  public AuthMetrics(MeterRegistry registry) {
    this.registry = registry;
  }

  public void loginAttempt(String result, String reason) {
    Counter.builder("auth.login.attempts")
        .description("Login attempts by result")
        .tag("result", result)
        .tag("reason", reason)
        .register(registry)
        .increment();
  }

  public void uniqueLogin() {
    Counter.builder("auth.unique.logins")
        .description("First successful login of a user in the current UTC day")
        .register(registry)
        .increment();
  }

  public void registration(String result) {
    Counter.builder("auth.registrations")
        .description("Account registrations by result")
        .tag("result", result)
        .register(registry)
        .increment();
  }

  public void tokenIssued(String type, String grant) {
    Counter.builder("auth.token.issued")
        .description("JWTs issued by token type and grant")
        .tag("type", type)
        .tag("grant", grant)
        .register(registry)
        .increment();
  }

  public void tokenRefresh(String result) {
    Counter.builder("auth.token.refresh")
        .description("Refresh-token exchanges by result")
        .tag("result", result)
        .register(registry)
        .increment();
  }

  public void passwordChange(String result) {
    Counter.builder("auth.password.changes")
        .description("Password changes by result")
        .tag("result", result)
        .register(registry)
        .increment();
  }

  public void logout() {
    Counter.builder("auth.logouts")
        .description("Logouts (all refresh tokens revoked)")
        .register(registry)
        .increment();
  }

  /** Times {@code action} as {@code auth_request_duration_seconds{operation,outcome}}. */
  public <T> T time(String operation, Supplier<T> action) {
    Timer.Sample sample = Timer.start(registry);
    String outcome = FAILURE;
    try {
      T result = action.get();
      outcome = SUCCESS;
      return result;
    } finally {
      sample.stop(
          Timer.builder("auth.request.duration")
              .description("Duration of auth operations")
              .tag("operation", operation)
              .tag("outcome", outcome)
              .publishPercentileHistogram()
              .register(registry));
    }
  }

  public void time(String operation, Runnable action) {
    time(
        operation,
        () -> {
          action.run();
          return null;
        });
  }

  /** Stable, non-reversible identifier for correlating a user across log lines. */
  public static String hash(String value) {
    if (value == null) {
      return null;
    }
    try {
      MessageDigest digest = MessageDigest.getInstance("SHA-256");
      byte[] bytes =
          digest.digest(value.trim().toLowerCase(Locale.ROOT).getBytes(StandardCharsets.UTF_8));
      return HexFormat.of().formatHex(bytes, 0, 8);
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException("SHA-256 not available", e);
    }
  }
}
