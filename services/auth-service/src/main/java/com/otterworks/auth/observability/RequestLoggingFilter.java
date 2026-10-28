package com.otterworks.auth.observability;

import static net.logstash.logback.argument.StructuredArguments.kv;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Propagates {@code X-Request-ID} (MDC key {@code request_id}) and writes one access log line per
 * request. Probe and scrape endpoints are not logged.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestLoggingFilter extends OncePerRequestFilter {

  public static final String REQUEST_ID_HEADER = "X-Request-ID";
  public static final String REQUEST_ID_MDC_KEY = "request_id";

  private static final Logger accessLog = LoggerFactory.getLogger("com.otterworks.auth.access");
  private static final Pattern SAFE_REQUEST_ID = Pattern.compile("[A-Za-z0-9._-]{1,128}");

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String requestId = resolveRequestId(request.getHeader(REQUEST_ID_HEADER));
    MDC.put(REQUEST_ID_MDC_KEY, requestId);
    response.setHeader(REQUEST_ID_HEADER, requestId);
    long start = System.nanoTime();
    int status = HttpServletResponse.SC_INTERNAL_SERVER_ERROR;
    try {
      filterChain.doFilter(request, response);
      status = response.getStatus();
    } finally {
      if (!isQuiet(request.getRequestURI())) {
        accessLog.info(
            "{} {} {}",
            kv("method", request.getMethod()),
            kv("path", request.getRequestURI()),
            kv("status", status),
            kv("duration_ms", (System.nanoTime() - start) / 1_000_000));
      }
      MDC.remove(REQUEST_ID_MDC_KEY);
    }
  }

  static String resolveRequestId(String incoming) {
    if (incoming != null && SAFE_REQUEST_ID.matcher(incoming).matches()) {
      return incoming;
    }
    return UUID.randomUUID().toString();
  }

  private static boolean isQuiet(String path) {
    return path.equals("/health") || path.equals("/metrics") || path.startsWith("/actuator");
  }
}
