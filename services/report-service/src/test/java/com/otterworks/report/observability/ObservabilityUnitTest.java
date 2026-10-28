package com.otterworks.report.observability;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.report.archive.ArchiveStoreRegistry;
import com.otterworks.report.controller.HealthController;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.nio.charset.StandardCharsets;
import java.sql.SQLException;
import java.util.Collections;
import java.util.Map;
import javax.servlet.FilterChain;
import javax.sql.DataSource;
import net.logstash.logback.encoder.LogstashEncoder;
import org.junit.After;
import org.junit.Test;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestTemplate;

public class ObservabilityUnitTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @After
    public void clearMdc() {
        MDC.clear();
    }

    @Test
    public void healthReturns503WhenDatabaseIsDown() throws Exception {
        DataSource dataSource = mock(DataSource.class);
        when(dataSource.getConnection()).thenThrow(new SQLException("connection refused"));
        HealthController controller = new HealthController(mock(ArchiveStoreRegistry.class), dataSource);

        ResponseEntity<Map<String, Object>> response = controller.health();

        assertEquals(503, response.getStatusCodeValue());
        assertEquals("degraded", response.getBody().get("status"));
        assertEquals(Collections.singletonMap("database", "down"), response.getBody().get("checks"));
    }

    @Test
    public void errorFilterReturnsJson500WithoutLeakingDetails() throws Exception {
        MDC.put(RequestContext.REQUEST_ID_MDC_KEY, "req-42");
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/v1/reports/1");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain failing = (req, res) -> {
            throw new IllegalStateException("secret db password in message");
        };

        new ErrorHandlingFilter().doFilter(request, response, failing);

        assertEquals(500, response.getStatus());
        JsonNode body = objectMapper.readTree(response.getContentAsString());
        assertEquals("Internal server error", body.get("message").asText());
        assertEquals("req-42", body.get("requestId").asText());
        assertFalse(response.getContentAsString().contains("secret"));
    }

    @Test
    public void restTemplateForwardsRequestId() {
        RestTemplate restTemplate = new RestTemplate();
        restTemplate.setInterceptors(Collections.singletonList(
                new ObservabilityConfig().requestIdPropagationInterceptor()));
        MockRestServiceServer server = MockRestServiceServer.bindTo(restTemplate).build();
        server.expect(requestTo("http://audit-service/api/v1/audit"))
                .andExpect(header(RequestContext.REQUEST_ID_HEADER, "req-7"))
                .andRespond(withSuccess());

        MDC.put(RequestContext.REQUEST_ID_MDC_KEY, "req-7");
        restTemplate.getForEntity("http://audit-service/api/v1/audit", String.class);

        server.verify();
    }

    @Test
    public void requestLoggingFilterLogsJsonWithRequestId() throws Exception {
        Logger logger = (Logger) LoggerFactory.getLogger(RequestLoggingFilter.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<ILoggingEvent>();
        appender.start();
        logger.addAppender(appender);
        try {
            MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/v1/reports");
            request.addHeader(RequestContext.REQUEST_ID_HEADER, "req-9");
            MockHttpServletResponse response = new MockHttpServletResponse();
            new RequestLoggingFilter().doFilter(request, response, (req, res) -> {
                assertEquals("req-9", MDC.get(RequestContext.REQUEST_ID_MDC_KEY));
            });

            assertEquals(1, appender.list.size());
            LogstashEncoder encoder = new LogstashEncoder();
            encoder.setCustomFields("{\"service\":\"report-service\"}");
            encoder.start();
            JsonNode json = objectMapper.readTree(
                    new String(encoder.encode(appender.list.get(0)), StandardCharsets.UTF_8));
            assertEquals("report-service", json.get("service").asText());
            assertEquals("req-9", json.get("requestId").asText());
            assertEquals("POST", json.get("method").asText());
            assertEquals("/api/v1/reports", json.get("path").asText());
            assertTrue(json.has("durationMs"));
            assertEquals(null, MDC.get(RequestContext.REQUEST_ID_MDC_KEY));
        } finally {
            logger.detachAppender(appender);
        }
    }

    @Test
    public void reportMetricsRecordsOutcomes() {
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        ReportMetrics metrics = new ReportMetrics(registry);

        metrics.requested("AUDIT_LOG", "PDF");
        metrics.generated("AUDIT_LOG", "PDF", "failure", 1_000_000L, 0);

        assertEquals(1.0, registry.get("report.requests").tag("type", "PDF").counter().count(), 0.0);
        assertEquals(1.0, registry.get("report.generated").tag("outcome", "failure").counter().count(), 0.0);
        assertEquals(1, registry.get("report.generation.duration").timer().count());
    }
}
