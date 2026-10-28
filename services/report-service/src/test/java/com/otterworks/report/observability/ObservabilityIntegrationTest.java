package com.otterworks.report.observability;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.is;
import static org.hamcrest.Matchers.matchesPattern;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.actuate.metrics.AutoConfigureMetrics;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.junit4.SpringRunner;
import org.springframework.test.web.servlet.MockMvc;

@RunWith(SpringRunner.class)
@SpringBootTest
@AutoConfigureMockMvc
@AutoConfigureMetrics
@ActiveProfiles("test")
public class ObservabilityIntegrationTest {

    private static final String UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

    @Autowired
    private MockMvc mockMvc;

    @Test
    public void metricsExposesPrometheusTextWithReportDomainMetrics() throws Exception {
        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reportName\":\"Metrics\",\"category\":\"AUDIT_LOG\","
                                + "\"reportType\":\"CSV\",\"requestedBy\":\"metrics-user\"}"))
                .andExpect(status().isAccepted());

        mockMvc.perform(get("/metrics"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith("text/plain"))
                .andExpect(content().string(containsString("# TYPE jvm_memory_used_bytes gauge")))
                .andExpect(content().string(containsString(
                        "report_requests_total{category=\"AUDIT_LOG\",type=\"CSV\",}")))
                .andExpect(content().string(containsString("http_server_requests_seconds")));
    }

    @Test
    public void healthReportsDatabaseUp() throws Exception {
        mockMvc.perform(get("/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status", is("healthy")))
                .andExpect(jsonPath("$.service", is("report-service")))
                .andExpect(jsonPath("$.checks.database", is("up")));
    }

    @Test
    public void generatesRequestIdWhenMissing() throws Exception {
        mockMvc.perform(get("/health"))
                .andExpect(header().string(RequestContext.REQUEST_ID_HEADER, matchesPattern(UUID_PATTERN)));
    }

    @Test
    public void echoesValidInboundRequestId() throws Exception {
        mockMvc.perform(get("/health").header(RequestContext.REQUEST_ID_HEADER, "abc-123.def"))
                .andExpect(header().string(RequestContext.REQUEST_ID_HEADER, "abc-123.def"));
    }

    @Test
    public void replacesInvalidInboundRequestId() throws Exception {
        mockMvc.perform(get("/health").header(RequestContext.REQUEST_ID_HEADER, "bad id\r\ninjected"))
                .andExpect(header().string(RequestContext.REQUEST_ID_HEADER, matchesPattern(UUID_PATTERN)));
    }
}
