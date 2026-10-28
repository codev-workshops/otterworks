package com.otterworks.auth.observability;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.tracing.Tracer;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.web.client.RestClientCustomizer;
import org.springframework.boot.web.client.RestTemplateCustomizer;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;
import org.springframework.http.client.ClientHttpRequestInterceptor;

@Configuration
public class ObservabilityConfig {

  // Runs right after Spring's ServerHttpObservationFilter (HIGHEST_PRECEDENCE + 1) so the
  // trace/span ids are already in the MDC, and well before the Spring Security filter chain.
  static final int ERROR_HANDLING_ORDER = Ordered.HIGHEST_PRECEDENCE + 2;
  static final int REQUEST_LOGGING_ORDER = Ordered.HIGHEST_PRECEDENCE + 3;

  @Bean
  public FilterRegistrationBean<ErrorHandlingFilter> errorHandlingFilter(
      ObjectMapper objectMapper) {
    FilterRegistrationBean<ErrorHandlingFilter> registration =
        new FilterRegistrationBean<>(new ErrorHandlingFilter(objectMapper));
    registration.setOrder(ERROR_HANDLING_ORDER);
    return registration;
  }

  @Bean
  public FilterRegistrationBean<RequestLoggingFilter> requestLoggingFilter(
      ObjectProvider<Tracer> tracer) {
    FilterRegistrationBean<RequestLoggingFilter> registration =
        new FilterRegistrationBean<>(new RequestLoggingFilter(tracer));
    registration.setOrder(REQUEST_LOGGING_ORDER);
    return registration;
  }

  /** Forwards the inbound request ID on outgoing calls; traceparent is added by Micrometer. */
  @Bean
  public ClientHttpRequestInterceptor requestIdPropagationInterceptor() {
    return (request, body, execution) -> {
      String requestId = RequestContext.currentRequestId();
      if (requestId != null
          && !request.getHeaders().containsKey(RequestContext.REQUEST_ID_HEADER)) {
        request.getHeaders().set(RequestContext.REQUEST_ID_HEADER, requestId);
      }
      return execution.execute(request, body);
    };
  }

  @Bean
  public RestTemplateCustomizer requestIdRestTemplateCustomizer(
      ClientHttpRequestInterceptor requestIdPropagationInterceptor) {
    return restTemplate -> restTemplate.getInterceptors().add(requestIdPropagationInterceptor);
  }

  @Bean
  public RestClientCustomizer requestIdRestClientCustomizer(
      ClientHttpRequestInterceptor requestIdPropagationInterceptor) {
    return builder -> builder.requestInterceptor(requestIdPropagationInterceptor);
  }
}
