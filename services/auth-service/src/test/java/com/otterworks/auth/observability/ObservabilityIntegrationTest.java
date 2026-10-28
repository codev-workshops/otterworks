package com.otterworks.auth.observability;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.web.client.RestClient;

@SpringBootTest
@AutoConfigureMockMvc
@AutoConfigureObservability
@ActiveProfiles("test")
@ExtendWith(OutputCaptureExtension.class)
class ObservabilityIntegrationTest {

  private static final String REGISTER_BODY =
      """
      {"email": "metrics@otterworks.dev", "password": "password123", "displayName": "Metrics"}
      """;

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;
  @Autowired private RestClient.Builder restClientBuilder;

  @BeforeEach
  void setUp() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  @Test
  void metrics_shouldExposePrometheusTextWithAuthDomainMetrics() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(REGISTER_BODY))
        .andExpect(status().isCreated());
    mockMvc
        .perform(
            post("/api/v1/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {"email": "metrics@otterworks.dev", "password": "wrong-password"}
                    """))
        .andExpect(status().isBadRequest());

    String body =
        mockMvc
            .perform(get("/metrics"))
            .andExpect(status().isOk())
            .andExpect(content().contentTypeCompatibleWith("text/plain"))
            .andReturn()
            .getResponse()
            .getContentAsString();

    assertThat(body)
        .contains("# TYPE auth_login_attempts_total counter")
        .containsPattern(
            "auth_login_attempts_total\\{[^}]*reason=\"invalid_password\"[^}]*result=\"failure\"")
        .containsPattern("auth_token_issued_total\\{[^}]*grant=\"register\"[^}]*type=\"access\"")
        .containsPattern("auth_registrations_total\\{[^}]*result=\"success\"")
        .containsPattern("auth_request_duration_seconds_bucket\\{[^}]*operation=\"login\"")
        .contains("http_server_requests_seconds");
  }

  @Test
  void requestId_shouldBeGeneratedWhenMissing() throws Exception {
    MvcResult result =
        mockMvc
            .perform(get("/health"))
            .andExpect(header().exists(RequestContext.REQUEST_ID_HEADER))
            .andReturn();

    String requestId = result.getResponse().getHeader(RequestContext.REQUEST_ID_HEADER);
    assertThat(UUID.fromString(requestId)).isNotNull();
  }

  @Test
  void requestId_shouldBeEchoedWhenValidAndReplacedWhenNot() throws Exception {
    mockMvc
        .perform(get("/health").header(RequestContext.REQUEST_ID_HEADER, "gw-123.abc"))
        .andExpect(header().string(RequestContext.REQUEST_ID_HEADER, "gw-123.abc"));

    MvcResult result =
        mockMvc
            .perform(get("/health").header(RequestContext.REQUEST_ID_HEADER, "bad id\r\ninjected"))
            .andReturn();
    assertThat(result.getResponse().getHeader(RequestContext.REQUEST_ID_HEADER))
        .isNotEqualTo("bad id\r\ninjected")
        .matches("[0-9a-f-]{36}");
  }

  @Test
  void logs_shouldBeJsonWithServiceRequestIdAndTraceId(CapturedOutput output) throws Exception {
    String requestId = "log-test-" + UUID.randomUUID();
    mockMvc
        .perform(
            post("/api/v1/auth/register")
                .header(RequestContext.REQUEST_ID_HEADER, requestId)
                .contentType(MediaType.APPLICATION_JSON)
                .content(REGISTER_BODY))
        .andExpect(status().isCreated());

    List<JsonNode> lines =
        Arrays.stream(output.getOut().split("\\R"))
            .filter(line -> line.startsWith("{") && line.contains(requestId))
            .map(this::parse)
            .toList();

    JsonNode authEvent =
        lines.stream()
            .filter(node -> "auth.register".equals(node.path("event").asText()))
            .findFirst()
            .orElseThrow(() -> new AssertionError("no auth.register log line in " + lines));
    assertThat(authEvent.path("service").asText()).isEqualTo("auth-service");
    assertThat(authEvent.path("outcome").asText()).isEqualTo("success");
    assertThat(authEvent.path("userIdHash").asText()).matches("[0-9a-f]{16}");
    assertThat(authEvent.path("traceId").asText()).isNotBlank();
    assertThat(authEvent.toString()).doesNotContain("metrics@otterworks.dev");

    JsonNode requestLog =
        lines.stream()
            .filter(node -> "/api/v1/auth/register".equals(node.path("path").asText()))
            .findFirst()
            .orElseThrow(() -> new AssertionError("no request log line in " + lines));
    assertThat(requestLog.path("statusCode").asInt()).isEqualTo(201);
    assertThat(requestLog.path("method").asText()).isEqualTo("POST");
    assertThat(requestLog.path("traceId").asText()).isEqualTo(authEvent.path("traceId").asText());
  }

  @Test
  void restClient_shouldPropagateRequestIdOnOutgoingCalls() {
    RestClient.Builder builder = restClientBuilder.clone();
    MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
    server
        .expect(requestTo("http://downstream/ping"))
        .andExpect(header(RequestContext.REQUEST_ID_HEADER, "outbound-42"))
        .andRespond(withSuccess());

    MDC.put(RequestContext.REQUEST_ID_MDC_KEY, "outbound-42");
    try {
      builder.build().get().uri("http://downstream/ping").retrieve().toBodilessEntity();
    } finally {
      MDC.remove(RequestContext.REQUEST_ID_MDC_KEY);
    }
    server.verify();
  }

  private JsonNode parse(String line) {
    try {
      return objectMapper.readTree(line);
    } catch (Exception e) {
      throw new AssertionError("log line is not JSON: " + line, e);
    }
  }
}
