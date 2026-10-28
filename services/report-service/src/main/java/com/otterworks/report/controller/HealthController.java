package com.otterworks.report.controller;

import com.otterworks.report.archive.ArchiveStoreRegistry;
import com.otterworks.report.archive.ArchiveStoreType;
import com.otterworks.report.archive.ArchiveStoreUnavailableException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.sql.Connection;
import java.sql.SQLException;
import javax.sql.DataSource;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Health check endpoint.
 */
@RestController
public class HealthController {

    private static final Logger logger = LoggerFactory.getLogger(HealthController.class);

    private final ArchiveStoreRegistry archive;
    private final DataSource dataSource;

    public HealthController(ArchiveStoreRegistry archive, DataSource dataSource) {
        this.archive = archive;
        this.dataSource = dataSource;
    }

    /** Checks the report database; 503 with {@code "status": "degraded"} when it is unreachable. */
    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        boolean databaseUp = databaseHealthy();
        Map<String, Object> checks = new LinkedHashMap<String, Object>();
        checks.put("database", databaseUp ? "up" : "down");
        Map<String, Object> response = new LinkedHashMap<String, Object>();
        response.put("status", databaseUp ? "healthy" : "degraded");
        response.put("service", "report-service");
        response.put("version", "0.1.0");
        response.put("checks", checks);
        return ResponseEntity.status(databaseUp ? HttpStatus.OK : HttpStatus.SERVICE_UNAVAILABLE).body(response);
    }

    private boolean databaseHealthy() {
        try (Connection connection = dataSource.getConnection()) {
            return connection.isValid(3);
        } catch (SQLException e) {
            logger.warn("Health check: database unreachable: {}", e.getMessage());
            return false;
        }
    }

    /**
     * Pings the selected archive store. Independent of {@link #health()} so the liveness /
     * readiness probes never depend on Db2 or Azure SQL being reachable.
     */
    @GetMapping("/health/archive")
    public ResponseEntity<Map<String, Object>> archiveHealth() {
        Map<String, Object> response = new LinkedHashMap<String, Object>();
        response.put("store", archive.type().wireName());
        response.put("namespace", archive.namespace());
        if (archive.type() == ArchiveStoreType.OFF) {
            response.put("status", "disabled");
            return ResponseEntity.ok(response);
        }
        try {
            archive.store().ping();
            response.put("status", "healthy");
            return ResponseEntity.ok(response);
        } catch (ArchiveStoreUnavailableException e) {
            response.put("status", "unavailable");
            response.put("detail", e.getMessage());
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(response);
        }
    }
}
