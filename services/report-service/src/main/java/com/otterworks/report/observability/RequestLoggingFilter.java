package com.otterworks.report.observability;

import static net.logstash.logback.argument.StructuredArguments.kv;

import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanContext;
import java.io.IOException;
import java.util.UUID;
import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Accepts or generates an {@code X-Request-ID}, echoes it on the response, puts it (and the active
 * trace/span IDs) into the logging MDC, and writes one access-log line per request.
 */
public class RequestLoggingFilter extends OncePerRequestFilter {

    private static final Logger logger = LoggerFactory.getLogger(RequestLoggingFilter.class);

    @Override
    protected void doFilterInternal(
            HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String requestId = request.getHeader(RequestContext.REQUEST_ID_HEADER);
        if (!RequestContext.isValidRequestId(requestId)) {
            requestId = UUID.randomUUID().toString();
        }
        MDC.put(RequestContext.REQUEST_ID_MDC_KEY, requestId);
        response.setHeader(RequestContext.REQUEST_ID_HEADER, requestId);

        Span span = Span.current();
        SpanContext spanContext = span.getSpanContext();
        if (spanContext.isValid()) {
            span.setAttribute("request.id", requestId);
            MDC.put(RequestContext.TRACE_ID_MDC_KEY, spanContext.getTraceId());
            MDC.put(RequestContext.SPAN_ID_MDC_KEY, spanContext.getSpanId());
        }

        long start = System.nanoTime();
        try {
            chain.doFilter(request, response);
        } finally {
            long durationMs = (System.nanoTime() - start) / 1_000_000;
            String path = request.getRequestURI();
            if (isProbe(path)) {
                logger.debug("HTTP {} {} responded {} in {} ms", request.getMethod(), path,
                        response.getStatus(), durationMs);
            } else {
                logger.info("HTTP request completed {} {} {} {}", kv("method", request.getMethod()),
                        kv("path", path), kv("status", response.getStatus()), kv("durationMs", durationMs));
            }
            MDC.remove(RequestContext.REQUEST_ID_MDC_KEY);
            MDC.remove(RequestContext.TRACE_ID_MDC_KEY);
            MDC.remove(RequestContext.SPAN_ID_MDC_KEY);
        }
    }

    private static boolean isProbe(String path) {
        return path.startsWith("/health") || "/metrics".equals(path) || path.startsWith("/actuator");
    }
}
