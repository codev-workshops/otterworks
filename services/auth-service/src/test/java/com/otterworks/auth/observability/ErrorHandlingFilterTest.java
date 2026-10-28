package com.otterworks.auth.observability;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class ErrorHandlingFilterTest {

  private final ObjectMapper objectMapper = new ObjectMapper();
  private final ErrorHandlingFilter filter = new ErrorHandlingFilter(objectMapper);

  @AfterEach
  void clearMdc() {
    MDC.clear();
  }

  @Test
  void shouldReturnJson500WithRequestIdForUncaughtException() throws Exception {
    MDC.put(RequestContext.REQUEST_ID_MDC_KEY, "req-1");
    MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/v1/auth/profile");
    MockHttpServletResponse response = new MockHttpServletResponse();

    filter.doFilter(
        request,
        response,
        (req, res) -> {
          throw new IllegalStateException("boom");
        });

    assertThat(response.getStatus()).isEqualTo(500);
    assertThat(response.getContentType()).isEqualTo("application/json");
    JsonNode body = objectMapper.readTree(response.getContentAsString());
    assertThat(body.path("status").asInt()).isEqualTo(500);
    assertThat(body.path("message").asText()).isEqualTo("Internal server error");
    assertThat(body.path("requestId").asText()).isEqualTo("req-1");
    assertThat(response.getContentAsString()).doesNotContain("boom");
  }

  @Test
  void shouldPassThroughWhenNoException() throws Exception {
    MockHttpServletResponse response = new MockHttpServletResponse();

    filter.doFilter(
        new MockHttpServletRequest("GET", "/health"),
        response,
        (req, res) -> ((MockHttpServletResponse) res).setStatus(204));

    assertThat(response.getStatus()).isEqualTo(204);
  }
}
