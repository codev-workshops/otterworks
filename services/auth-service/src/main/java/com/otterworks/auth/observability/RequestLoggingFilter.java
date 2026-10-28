package com.otterworks.auth.observability;

import static net.logstash.logback.argument.StructuredArguments.kv;

import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Port of audit-service's RequestLoggingMiddleware. Accepts or generates an {@code X-Request-ID},
 * exposes it in the MDC, on the active span and on the response, and logs one line per request.
 */
public class RequestLoggingFilter extends OncePerRequestFilter {

  private static final Logger log = LoggerFactory.getLogger(RequestLoggingFilter.class);

  private final ObjectProvider<Tracer> tracer;

  public RequestLoggingFilter(ObjectProvider<Tracer> tracer) {
    this.tracer = tracer;
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String requestId = request.getHeader(RequestContext.REQUEST_ID_HEADER);
    if (!RequestContext.isValidRequestId(requestId)) {
      requestId = UUID.randomUUID().toString();
    }

    MDC.put(RequestContext.REQUEST_ID_MDC_KEY, requestId);
    response.setHeader(RequestContext.REQUEST_ID_HEADER, requestId);
    tagCurrentSpan(requestId);

    long start = System.nanoTime();
    String method = request.getMethod();
    String path = request.getRequestURI();
    try {
      filterChain.doFilter(request, response);
      long elapsedMs = (System.nanoTime() - start) / 1_000_000;
      if (isProbe(path)) {
        log.debug(
            "HTTP {} {} responded {} in {}ms",
            kv("method", method),
            kv("path", path),
            kv("statusCode", response.getStatus()),
            kv("elapsedMs", elapsedMs));
      } else {
        log.info(
            "HTTP {} {} responded {} in {}ms",
            kv("method", method),
            kv("path", path),
            kv("statusCode", response.getStatus()),
            kv("elapsedMs", elapsedMs));
      }
    } catch (IOException | ServletException | RuntimeException ex) {
      long elapsedMs = (System.nanoTime() - start) / 1_000_000;
      log.error(
          "HTTP {} {} failed after {}ms",
          kv("method", method),
          kv("path", path),
          kv("elapsedMs", elapsedMs),
          ex);
      throw ex;
    } finally {
      MDC.remove(RequestContext.REQUEST_ID_MDC_KEY);
    }
  }

  private void tagCurrentSpan(String requestId) {
    Tracer activeTracer = tracer.getIfAvailable();
    if (activeTracer == null) {
      return;
    }
    Span span = activeTracer.currentSpan();
    if (span != null) {
      span.tag("request.id", requestId);
    }
  }

  private static boolean isProbe(String path) {
    return "/health".equals(path) || "/metrics".equals(path);
  }
}
