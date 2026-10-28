package com.otterworks.auth.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.sql.Connection;
import java.sql.SQLException;
import java.util.Map;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;

class HealthControllerTest {

  @Test
  void health_shouldReturn200WhenDatabaseIsReachable() throws Exception {
    DataSource dataSource = mock(DataSource.class);
    Connection connection = mock(Connection.class);
    when(dataSource.getConnection()).thenReturn(connection);
    when(connection.isValid(3)).thenReturn(true);

    ResponseEntity<Map<String, Object>> response = new HealthController(dataSource).health();

    assertThat(response.getStatusCode().value()).isEqualTo(200);
    assertThat(response.getBody()).containsEntry("status", "healthy");
    assertThat(response.getBody()).containsEntry("database", Map.of("status", "up"));
  }

  @Test
  void health_shouldReturn503WhenDatabaseIsUnreachable() throws Exception {
    DataSource dataSource = mock(DataSource.class);
    when(dataSource.getConnection()).thenThrow(new SQLException("connection refused"));

    ResponseEntity<Map<String, Object>> response = new HealthController(dataSource).health();

    assertThat(response.getStatusCode().value()).isEqualTo(503);
    assertThat(response.getBody()).containsEntry("status", "degraded");
    assertThat(response.getBody()).containsEntry("database", Map.of("status", "down"));
  }
}
