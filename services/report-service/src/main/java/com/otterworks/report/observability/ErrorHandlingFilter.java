package com.otterworks.report.observability;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.filter.OncePerRequestFilter;

/** Turns exceptions that escape the servlet chain into a logged JSON 500 without leaking details. */
public class ErrorHandlingFilter extends OncePerRequestFilter {

    private static final Logger logger = LoggerFactory.getLogger(ErrorHandlingFilter.class);

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Override
    protected void doFilterInternal(
            HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        try {
            chain.doFilter(request, response);
        } catch (IOException | ServletException | RuntimeException e) {
            logger.error("Unhandled exception processing {} {}", request.getMethod(), request.getRequestURI(), e);
            if (response.isCommitted()) {
                throw e;
            }
            HttpStatus status = HttpStatus.INTERNAL_SERVER_ERROR;
            Map<String, Object> body = new LinkedHashMap<String, Object>();
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
