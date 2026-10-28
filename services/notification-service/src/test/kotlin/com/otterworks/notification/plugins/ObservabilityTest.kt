package com.otterworks.notification.plugins

import ch.qos.logback.classic.Logger
import ch.qos.logback.classic.spi.ILoggingEvent
import ch.qos.logback.core.read.ListAppender
import com.otterworks.notification.configurePlugins
import com.otterworks.notification.config.AppConfig
import com.otterworks.notification.routes.configureRouting
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.websocket.WebSocketManager
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.statement.bodyAsText
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.install
import io.ktor.server.testing.ApplicationTestBuilder
import io.ktor.server.testing.testApplication
import io.micrometer.core.instrument.MeterRegistry
import io.mockk.coEvery
import io.mockk.mockk
import io.opentelemetry.api.common.AttributeKey
import io.opentelemetry.sdk.OpenTelemetrySdk
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter
import io.opentelemetry.sdk.trace.SdkTracerProvider
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.koin.core.context.stopKoin
import org.koin.dsl.module
import org.koin.ktor.plugin.Koin
import org.slf4j.LoggerFactory
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue

class ObservabilityTest {

    private val uuid = Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    private val spans = InMemorySpanExporter.create()
    private val openTelemetry = OpenTelemetrySdk.builder()
        .setTracerProvider(SdkTracerProvider.builder().addSpanProcessor(SimpleSpanProcessor.create(spans)).build())
        .build()

    private fun ApplicationTestBuilder.notificationApp(healthChecker: HealthChecker, service: NotificationService) {
        application {
            stopKoin()
            val registry = configureMonitoring()
            configureObservability(openTelemetry)
            configurePlugins(AppConfig.load())
            install(Koin) {
                modules(
                    module {
                        single { registry as MeterRegistry }
                        single { service }
                        single { mockk<WebSocketManager>(relaxed = true) }
                        single { healthChecker }
                    },
                )
            }
            configureRouting(registry)
        }
    }

    @AfterTest
    fun tearDown() {
        stopKoin()
    }

    private val healthy = HealthChecker(mapOf("dynamodb" to suspend { "ok" }))

    @Test
    fun `health is 200 when DynamoDB is reachable and 503 when it is not`() {
        testApplication {
            notificationApp(healthy, mockk(relaxed = true))
            val response = client.get("/health")
            assertEquals(HttpStatusCode.OK, response.status)
            val body = Json.parseToJsonElement(response.bodyAsText()).jsonObject
            assertEquals("healthy", body["status"]!!.jsonPrimitive.content)
            assertEquals("up", body["checks"]!!.jsonObject["dynamodb"]!!.jsonPrimitive.content)
        }
        testApplication {
            notificationApp(
                HealthChecker(mapOf("dynamodb" to suspend { throw IllegalStateException("unreachable") })),
                mockk(relaxed = true),
            )
            val response = client.get("/health")
            assertEquals(HttpStatusCode.ServiceUnavailable, response.status)
            assertTrue(response.bodyAsText().contains("\"degraded\""))
        }
    }

    @Test
    fun `metrics serves Prometheus text including HTTP server metrics`() = testApplication {
        notificationApp(healthy, mockk(relaxed = true))
        client.get("/api/v1/notifications/unread-count?user_id=u1")
        val body = client.get("/metrics").bodyAsText()
        assertTrue(body.contains("# TYPE jvm_memory_used_bytes gauge"), body.take(500))
        assertTrue(body.contains("ktor_http_server_requests_seconds"), "missing ktor request metrics")
    }

    @Test
    fun `request id is echoed, generated when missing, and replaced when invalid`() = testApplication {
        notificationApp(healthy, mockk(relaxed = true))
        assertEquals("abc-123.def", client.get("/health") { header(REQUEST_ID_HEADER, "abc-123.def") }
            .headers[REQUEST_ID_HEADER])
        assertTrue(uuid.matches(client.get("/health").headers[REQUEST_ID_HEADER]!!))
        val replaced = client.get("/health") { header(REQUEST_ID_HEADER, "bad id!") }.headers[REQUEST_ID_HEADER]!!
        assertNotEquals("bad id!", replaced)
        assertTrue(uuid.matches(replaced))
    }

    @Test
    fun `access log carries request and trace ids and the span is tagged with the request id`() {
        val logger = LoggerFactory.getLogger("com.otterworks.notification.http") as Logger
        val appender = ListAppender<ILoggingEvent>().apply { start() }
        logger.addAppender(appender)
        try {
            testApplication {
                notificationApp(healthy, mockk(relaxed = true))
                client.get("/api/v1/notifications/unread-count?user_id=u1") { header(REQUEST_ID_HEADER, "req-77") }
            }
            val event = appender.list.single { it.formattedMessage.startsWith("HTTP GET") }
            assertEquals("req-77", event.mdcPropertyMap["requestId"])
            val span = spans.finishedSpanItems.single { it.attributes.get(AttributeKey.stringKey("request.id")) == "req-77" }
            assertEquals(span.traceId, event.mdcPropertyMap["traceId"])
        } finally {
            logger.detachAppender(appender)
        }
    }

    @Test
    fun `unhandled errors return JSON 500 with the request id and no exception detail`() = testApplication {
        val service = mockk<NotificationService>()
        coEvery { service.getUnreadCount(any()) } throws IllegalStateException("secret table name")
        notificationApp(healthy, service)
        val response = client.get("/api/v1/notifications/unread-count?user_id=u1") { header(REQUEST_ID_HEADER, "req-500") }
        assertEquals(HttpStatusCode.InternalServerError, response.status)
        val body = response.bodyAsText()
        assertTrue(body.contains("\"requestId\":\"req-500\""), body)
        assertTrue(!body.contains("secret"))
    }
}
