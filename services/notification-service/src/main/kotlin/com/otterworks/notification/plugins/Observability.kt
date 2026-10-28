package com.otterworks.notification.plugins

import io.ktor.server.application.Application
import io.ktor.server.application.ApplicationCallPipeline
import io.ktor.server.application.call
import io.ktor.server.application.install
import io.ktor.server.plugins.callid.CallId
import io.ktor.server.plugins.callid.callId
import io.ktor.server.plugins.callid.callIdMdc
import io.ktor.server.plugins.callloging.CallLogging
import io.ktor.server.request.httpMethod
import io.ktor.server.request.path
import io.opentelemetry.api.OpenTelemetry
import io.opentelemetry.api.trace.Span
import io.opentelemetry.instrumentation.ktor.v2_0.server.KtorServerTracing
import io.opentelemetry.sdk.autoconfigure.AutoConfiguredOpenTelemetrySdk
import kotlinx.coroutines.withTimeout
import mu.KotlinLogging
import org.slf4j.LoggerFactory
import org.slf4j.event.Level
import java.util.UUID

const val REQUEST_ID_HEADER = "X-Request-ID"
private val VALID_REQUEST_ID = Regex("^[A-Za-z0-9._:-]{1,128}$")
private val PROBE_PATHS = setOf("/health", "/metrics")
private val logger = KotlinLogging.logger {}

/**
 * OTLP/HTTP tracing configured by the standard `OTEL_*` env vars; `TRACING_ENABLED=false` returns a no-op.
 * Prometheus already covers metrics, so the OTel metrics/logs exporters are off.
 */
fun buildOpenTelemetry(enabled: Boolean = System.getenv("TRACING_ENABLED") != "false"): OpenTelemetry {
    if (!enabled) return OpenTelemetry.noop()
    val defaults = mapOf(
        "otel.service.name" to "notification-service",
        "otel.exporter.otlp.protocol" to "http/protobuf",
        "otel.metrics.exporter" to "none",
        "otel.logs.exporter" to "none",
    )
    return AutoConfiguredOpenTelemetrySdk.builder()
        .addPropertiesSupplier { defaults }
        .build()
        .openTelemetrySdk
}

/**
 * Server spans, `X-Request-ID` accept/generate/echo, request ID on the span, and one access log line per
 * request with `requestId`/`traceId`/`spanId` in the MDC.
 */
fun Application.configureObservability(openTelemetry: OpenTelemetry) {
    install(KtorServerTracing) {
        setOpenTelemetry(openTelemetry)
    }
    install(CallId) {
        retrieve { call -> call.request.headers[REQUEST_ID_HEADER]?.takeIf { VALID_REQUEST_ID.matches(it) } }
        generate { UUID.randomUUID().toString() }
        verify { VALID_REQUEST_ID.matches(it) }
        replyToHeader(REQUEST_ID_HEADER)
    }
    install(CallLogging) {
        logger = LoggerFactory.getLogger("com.otterworks.notification.http")
        level = Level.INFO
        callIdMdc("requestId")
        mdc("traceId") { Span.current().spanContext.takeIf { it.isValid }?.traceId }
        mdc("spanId") { Span.current().spanContext.takeIf { it.isValid }?.spanId }
        filter { call -> call.request.path() !in PROBE_PATHS }
        format { call ->
            "HTTP ${call.request.httpMethod.value} ${call.request.path()} responded ${call.response.status()?.value}"
        }
    }
    intercept(ApplicationCallPipeline.Monitoring) {
        call.callId?.let { Span.current().setAttribute("request.id", it) }
    }
}

/** Runs named dependency probes; a probe that throws or exceeds the timeout reports "down". */
class HealthChecker(
    private val checks: Map<String, suspend () -> Any?>,
    private val timeoutMs: Long = 3000,
) {
    suspend fun run(): Map<String, String> = checks.mapValues { (name, check) ->
        try {
            withTimeout(timeoutMs) { check() }
            "up"
        } catch (e: Exception) {
            logger.warn { "Health check '$name' failed: ${e.message}" }
            "down"
        }
    }
}
