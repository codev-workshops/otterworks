package com.otterworks.report.observability;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.instrumentation.spring.webmvc.v5_3.SpringWebMvcTelemetry;
import io.opentelemetry.sdk.autoconfigure.AutoConfiguredOpenTelemetrySdk;
import java.util.HashMap;
import java.util.Map;
import javax.servlet.Filter;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;
import org.springframework.http.client.ClientHttpRequestInterceptor;

/**
 * OpenTelemetry tracing (OTLP/HTTP, configured by the standard {@code OTEL_*} env vars) plus the
 * request-logging, error-handling and request-ID propagation wiring.
 */
@Configuration
public class ObservabilityConfig {

    @Bean(destroyMethod = "")
    public OpenTelemetry openTelemetry(
            @Value("${otterworks.tracing.enabled:true}") boolean tracingEnabled,
            @Value("${spring.application.name:report-service}") String serviceName) {
        if (!tracingEnabled) {
            return OpenTelemetry.noop();
        }
        Map<String, String> defaults = new HashMap<String, String>();
        defaults.put("otel.service.name", serviceName);
        defaults.put("otel.exporter.otlp.protocol", "http/protobuf");
        defaults.put("otel.metrics.exporter", "none");
        defaults.put("otel.logs.exporter", "none");
        return AutoConfiguredOpenTelemetrySdk.builder()
                .addPropertiesSupplier(() -> defaults)
                .setResultAsGlobal()
                .build()
                .getOpenTelemetrySdk();
    }

    @Bean
    public FilterRegistrationBean<ErrorHandlingFilter> errorHandlingFilter() {
        FilterRegistrationBean<ErrorHandlingFilter> bean = new FilterRegistrationBean<>(new ErrorHandlingFilter());
        bean.setOrder(Ordered.HIGHEST_PRECEDENCE);
        return bean;
    }

    @Bean
    public FilterRegistrationBean<Filter> tracingFilter(OpenTelemetry openTelemetry) {
        FilterRegistrationBean<Filter> bean =
                new FilterRegistrationBean<>(SpringWebMvcTelemetry.create(openTelemetry).createServletFilter());
        bean.setOrder(Ordered.HIGHEST_PRECEDENCE + 1);
        return bean;
    }

    @Bean
    public FilterRegistrationBean<RequestLoggingFilter> requestLoggingFilter() {
        FilterRegistrationBean<RequestLoggingFilter> bean = new FilterRegistrationBean<>(new RequestLoggingFilter());
        bean.setOrder(Ordered.HIGHEST_PRECEDENCE + 2);
        return bean;
    }

    /** Forwards the inbound {@code X-Request-ID} on outgoing RestTemplate calls. */
    @Bean
    public ClientHttpRequestInterceptor requestIdPropagationInterceptor() {
        return (request, body, execution) -> {
            String requestId = RequestContext.currentRequestId();
            if (requestId != null && !request.getHeaders().containsKey(RequestContext.REQUEST_ID_HEADER)) {
                request.getHeaders().set(RequestContext.REQUEST_ID_HEADER, requestId);
            }
            return execution.execute(request, body);
        };
    }
}
