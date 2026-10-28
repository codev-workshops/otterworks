package com.otterworks.report.observability;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.util.concurrent.TimeUnit;
import org.springframework.stereotype.Component;

/** Report-domain Prometheus metrics ({@code report_*}). */
@Component
public class ReportMetrics {

    private final MeterRegistry registry;

    public ReportMetrics(MeterRegistry registry) {
        this.registry = registry;
    }

    public void requested(String category, String type) {
        Counter.builder("report.requests")
                .description("Report generation requests accepted")
                .tag("category", category)
                .tag("type", type)
                .register(registry)
                .increment();
    }

    public void generated(String category, String type, String outcome, long durationNanos, int rows) {
        Timer.builder("report.generation.duration")
                .description("Wall time to fetch data and render a report")
                .tag("category", category)
                .tag("type", type)
                .tag("outcome", outcome)
                .publishPercentileHistogram()
                .register(registry)
                .record(durationNanos, TimeUnit.NANOSECONDS);
        Counter.builder("report.generated")
                .description("Reports that finished generating, by outcome")
                .tag("category", category)
                .tag("type", type)
                .tag("outcome", outcome)
                .register(registry)
                .increment();
        if (rows > 0) {
            registry.summary("report.rows", "type", type).record(rows);
        }
    }

    public void deleted() {
        registry.counter("report.deletions").increment();
    }
}
