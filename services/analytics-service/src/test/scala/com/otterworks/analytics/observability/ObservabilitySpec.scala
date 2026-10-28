package com.otterworks.analytics.observability

import akka.http.scaladsl.model.StatusCodes
import akka.http.scaladsl.model.headers.RawHeader
import akka.http.scaladsl.server.Directives.*
import akka.http.scaladsl.server.Route
import akka.http.scaladsl.testkit.ScalatestRouteTest
import io.opentelemetry.api.common.AttributeKey
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator
import io.opentelemetry.context.propagation.ContextPropagators
import io.opentelemetry.sdk.OpenTelemetrySdk
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter
import io.opentelemetry.sdk.trace.SdkTracerProvider
import io.opentelemetry.sdk.trace.`export`.SimpleSpanProcessor
import io.prometheus.client.CollectorRegistry
import org.scalatest.LoneElement
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import scala.jdk.CollectionConverters.*

class ObservabilitySpec extends AnyFlatSpec with Matchers with LoneElement with ScalatestRouteTest:

  private val exporter = InMemorySpanExporter.create()
  private val otel = OpenTelemetrySdk.builder()
    .setTracerProvider(
      SdkTracerProvider.builder().addSpanProcessor(SimpleSpanProcessor.create(exporter)).build())
    .setPropagators(ContextPropagators.create(W3CTraceContextPropagator.getInstance()))
    .build()

  private val UuidRe = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

  private val inner: Route = concat(
    path("api" / "v1" / "analytics" / "users" / Segment / "activity") { _ => complete("ok") },
    path("health") { complete("up") },
    path("boom") { get { throw new IllegalStateException("db password=hunter2") } },
  )
  private val routes = Observability.instrument(otel)(inner)

  private def durationCount(route: String, status: String): Double =
    Option(CollectorRegistry.defaultRegistry.getSampleValue(
      "analytics_request_duration_seconds_count",
      Array("method", "path", "status"),
      Array("GET", route, status),
    )).map(_.doubleValue).getOrElse(0.0)

  "Observability" should "echo a valid X-Request-ID and tag the server span" in {
    exporter.reset()
    val traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    Get("/api/v1/analytics/users/user-42/activity")
      .withHeaders(RawHeader("X-Request-ID", "req-1.a:b"), RawHeader("traceparent", traceparent)) ~>
      routes ~> check {
        status shouldBe StatusCodes.OK
        header("X-Request-ID").map(_.value) shouldBe Some("req-1.a:b")
      }
    val span = exporter.getFinishedSpanItems.asScala.loneElement
    span.getName shouldBe "GET /api/v1/analytics/users/:id/activity"
    span.getTraceId shouldBe "0af7651916cd43dd8448eb211c80319c"
    span.getAttributes.get(AttributeKey.stringKey("request.id")) shouldBe "req-1.a:b"
  }

  it should "replace a missing or invalid request ID with a UUID" in {
    Get("/health") ~> routes ~> check {
      header("X-Request-ID").map(_.value).get should fullyMatch regex UuidRe
    }
    Get("/health").withHeaders(RawHeader("X-Request-ID", "bad id <x>")) ~> routes ~> check {
      header("X-Request-ID").map(_.value).get should fullyMatch regex UuidRe
    }
  }

  it should "record request duration with a bounded route label" in {
    val before = durationCount("/api/v1/analytics/users/:id/activity", "200")
    Get("/api/v1/analytics/users/7f3c9a/activity") ~> routes ~> check {
      status shouldBe StatusCodes.OK
    }
    durationCount("/api/v1/analytics/users/:id/activity", "200") shouldBe before + 1
  }

  it should "return a JSON 500 with the request ID and no exception detail" in {
    Get("/boom").withHeaders(RawHeader("X-Request-ID", "req-500")) ~> routes ~> check {
      status shouldBe StatusCodes.InternalServerError
      val body = responseAs[String]
      body shouldBe """{"error":"Internal server error","requestId":"req-500"}"""
      body should not include "hunter2"
      header("X-Request-ID").map(_.value) shouldBe Some("req-500")
    }
  }

  it should "keep the default 404 for unknown routes and still echo the request ID" in {
    Get("/nope") ~> routes ~> check {
      status shouldBe StatusCodes.NotFound
      header("X-Request-ID") shouldBe defined
    }
  }

  it should "disable tracing when TRACING_ENABLED=false" in {
    val noop = Observability.initOpenTelemetry(Map("TRACING_ENABLED" -> "false"))
    noop.getTracer("t").spanBuilder("x").startSpan().getSpanContext.isValid shouldBe false
  }
