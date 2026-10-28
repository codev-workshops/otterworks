package com.otterworks.auth.observability;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@AutoConfigureObservability(tracing = false)
@ActiveProfiles("test")
class ObservabilityIntegrationTest {

  @Autowired private MockMvc mockMvc;

  @Test
  void echoesSafeRequestIdAndReplacesUnsafeOne() throws Exception {
    mockMvc
        .perform(get("/health").header("X-Request-ID", "req-123"))
        .andExpect(header().string("X-Request-ID", "req-123"));

    String generated =
        mockMvc
            .perform(get("/health").header("X-Request-ID", "bad id\nwith newline"))
            .andReturn()
            .getResponse()
            .getHeader("X-Request-ID");
    assertThat(generated).isNotBlank().doesNotContain(" ").doesNotContain("\n");
  }

  @Test
  void exposesLoginCountersAndLatencyHistogram() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"email\":\"nobody@otterworks.dev\",\"password\":\"wrong-password\"}"))
        .andExpect(status().isBadRequest());

    String metrics =
        mockMvc
            .perform(get("/metrics"))
            .andExpect(status().isOk())
            .andReturn()
            .getResponse()
            .getContentAsString();

    assertThat(metrics).contains("auth_logins_total{").contains("outcome=\"unknown_user\"");
    assertThat(metrics).contains("http_server_requests_seconds_bucket{");
  }

  @Test
  void resolveRequestIdRejectsOversizedValues() {
    assertThat(RequestLoggingFilter.resolveRequestId("a".repeat(129))).hasSize(36);
    assertThat(RequestLoggingFilter.resolveRequestId("abc-DEF_1.2")).isEqualTo("abc-DEF_1.2");
    assertThat(RequestLoggingFilter.resolveRequestId(null)).hasSize(36);
  }
}
