package com.otterworks.auth.observability;

import static net.logstash.logback.argument.StructuredArguments.kv;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Port of audit-service's ErrorHandlingMiddleware: catches exceptions that escape the filter chain
 * (outside {@code GlobalExceptionHandler}'s reach) and returns a JSON 500 carrying the request ID.
 */
public class ErrorHandlingFilter extends OncePerRequestFilter {

  private static final Logger log = LoggerFactory.getLogger(ErrorHandlingFilter.class);

  private final ObjectMapper objectMapper;

  public ErrorHandlingFilter(ObjectMapper objectMapper) {
    this.objectMapper = objectMapper;
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    try {
      filterChain.doFilter(request, response);
    } catch (IOException | ServletException | RuntimeException ex) {
      log.error(
          "Unhandled exception processing {} {}",
          kv("method", request.getMethod()),
          kv("path", request.getRequestURI()),
          ex);

      if (response.isCommitted()) {
        log.warn("Response has already started, cannot write error response");
        throw ex;
      }

      HttpStatus status = HttpStatus.INTERNAL_SERVER_ERROR;
      Map<String, Object> body = new LinkedHashMap<>();
      body.put("timestamp", Instant.now().toString());
      body.put("status", status.value());
      body.put("error", status.getReasonPhrase());
      body.put("message", "Internal server error");
      body.put("requestId", RequestContext.currentRequestId());

      response.resetBuffer();
      response.setStatus(status.value());
      response.setContentType(MediaType.APPLICATION_JSON_VALUE);
      objectMapper.writeValue(response.getOutputStream(), body);
    }
  }
}
