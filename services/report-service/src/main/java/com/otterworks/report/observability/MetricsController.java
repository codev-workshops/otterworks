package com.otterworks.report.observability;

import io.micrometer.prometheus.PrometheusMeterRegistry;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Serves Prometheus text format at {@code /metrics}, the path Prometheus and the ServiceMonitor scrape. */
@RestController
public class MetricsController {

    private static final MediaType PROMETHEUS_TEXT = MediaType.parseMediaType("text/plain;version=0.0.4;charset=utf-8");

    private final ObjectProvider<PrometheusMeterRegistry> registry;

    public MetricsController(ObjectProvider<PrometheusMeterRegistry> registry) {
        this.registry = registry;
    }

    @GetMapping("/metrics")
    public ResponseEntity<String> metrics() {
        PrometheusMeterRegistry prometheus = registry.getIfAvailable();
        if (prometheus == null) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body("prometheus registry disabled\n");
        }
        return ResponseEntity.ok().contentType(PROMETHEUS_TEXT).body(prometheus.scrape());
    }
}
