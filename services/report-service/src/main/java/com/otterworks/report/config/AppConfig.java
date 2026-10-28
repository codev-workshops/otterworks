package com.otterworks.report.config;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.instrumentation.spring.web.v3_1.SpringWebTelemetry;
import org.apache.http.impl.client.CloseableHttpClient;
import org.apache.http.impl.client.HttpClients;
import org.apache.http.impl.conn.PoolingHttpClientConnectionManager;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.ClientHttpRequestInterceptor;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

import java.util.Arrays;

/**
 * Application configuration — wires up RestTemplate and external service URLs.
 *
 * LEGACY PATTERNS:
 * - Uses RestTemplate (deprecated in Spring 5.x, removed path in 6.x)
 * - Uses Apache HttpComponents 4.x directly
 * - Manual connection pool management instead of reactive WebClient
 *
 * UPGRADE NOTES:
 * - Replace RestTemplate with WebClient (reactive) or RestClient (Spring 6.1+)
 * - Replace Apache HttpComponents with Reactor Netty or JDK HttpClient
 */
@Configuration
public class AppConfig {

    @Value("${otterworks.analytics-service.url:http://analytics-service:8088}")
    private String analyticsServiceUrl;

    @Value("${otterworks.audit-service.url:http://audit-service:8090}")
    private String auditServiceUrl;

    @Value("${otterworks.auth-service.url:http://auth-service:8081}")
    private String authServiceUrl;

    @Value("${otterworks.report.output-dir:/tmp/reports}")
    private String reportOutputDir;

    @Value("${otterworks.report.max-rows:50000}")
    private int maxRows;

    @Value("${otterworks.report.connection-timeout:5000}")
    private int connectionTimeout;

    @Value("${otterworks.report.read-timeout:30000}")
    private int readTimeout;

    // LEGACY: RestTemplate with Apache HttpComponents 4.x connection pool
    @Bean
    public RestTemplate restTemplate(
            OpenTelemetry openTelemetry, ClientHttpRequestInterceptor requestIdPropagationInterceptor) {
        PoolingHttpClientConnectionManager connectionManager = new PoolingHttpClientConnectionManager();
        connectionManager.setMaxTotal(50);
        connectionManager.setDefaultMaxPerRoute(20);

        CloseableHttpClient httpClient = HttpClients.custom()
                .setConnectionManager(connectionManager)
                .build();

        HttpComponentsClientHttpRequestFactory factory = new HttpComponentsClientHttpRequestFactory(httpClient);
        factory.setConnectTimeout(connectionTimeout);
        factory.setReadTimeout(readTimeout);

        RestTemplate restTemplate = new RestTemplate(factory);
        restTemplate.setInterceptors(Arrays.asList(
                SpringWebTelemetry.create(openTelemetry).newInterceptor(), requestIdPropagationInterceptor));
        return restTemplate;
    }

    public String getAnalyticsServiceUrl() {
        return analyticsServiceUrl;
    }

    public String getAuditServiceUrl() {
        return auditServiceUrl;
    }

    public String getAuthServiceUrl() {
        return authServiceUrl;
    }

    public String getReportOutputDir() {
        return reportOutputDir;
    }

    public int getMaxRows() {
        return maxRows;
    }
}
