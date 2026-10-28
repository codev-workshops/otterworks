package com.otterworks.analytics.api

import akka.http.scaladsl.model.StatusCodes
import akka.http.scaladsl.model.headers.RawHeader
import akka.http.scaladsl.server.Directives.*
import akka.http.scaladsl.testkit.ScalatestRouteTest
import com.otterworks.analytics.config.PostgresConfig
import com.otterworks.analytics.repository.MetricsRepository
import com.otterworks.analytics.service.AnalyticsService
import io.prometheus.client.CollectorRegistry
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

class HttpMetricsSpec extends AnyFlatSpec with Matchers with ScalatestRouteTest:

  private def sample(name: String, labels: (String, String)*): Double =
    Option(CollectorRegistry.defaultRegistry.getSampleValue(
      name, labels.map(_._1).toArray, labels.map(_._2).toArray)).map(_.doubleValue).getOrElse(0.0)

  private val inner = concat(
    path("ok") { get { complete("ok") } },
    path("boom") { get { failWith(new RuntimeException("boom")) } },
  )
  private val route = HttpMetrics.instrument(inner)

  "pathLabel" should "template id segments and collapse unmatched paths" in {
    HttpMetrics.pathLabel("/api/v1/analytics/users/u-123/activity", 200) shouldBe "/api/v1/analytics/users/:id/activity"
    HttpMetrics.pathLabel("/api/v1/analytics/documents/d-9/stats", 200) shouldBe "/api/v1/analytics/documents/:id/stats"
    HttpMetrics.pathLabel("/api/v1/analytics/dashboard", 200) shouldBe "/api/v1/analytics/dashboard"
    HttpMetrics.pathLabel("/random/garbage", 404) shouldBe "unmatched"
  }

  "instrument" should "count requests with method, path and status labels" in {
    val before = sample("http_requests_total", "method" -> "GET", "path" -> "/ok", "status" -> "200")
    Get("/ok") ~> route ~> check { status shouldBe StatusCodes.OK }
    sample("http_requests_total", "method" -> "GET", "path" -> "/ok", "status" -> "200") shouldBe before + 1
    sample("http_request_duration_seconds_count", "method" -> "GET", "path" -> "/ok", "status" -> "200") should be >= 1.0
  }

  it should "record rejections as 404 unmatched and exceptions as 500" in {
    val before404 = sample("http_requests_total", "method" -> "GET", "path" -> "unmatched", "status" -> "404")
    val before500 = sample("http_requests_total", "method" -> "GET", "path" -> "/boom", "status" -> "500")
    Get("/nope") ~> route ~> check { status shouldBe StatusCodes.NotFound }
    Get("/boom") ~> route ~> check { status shouldBe StatusCodes.InternalServerError }
    sample("http_requests_total", "method" -> "GET", "path" -> "unmatched", "status" -> "404") shouldBe before404 + 1
    sample("http_requests_total", "method" -> "GET", "path" -> "/boom", "status" -> "500") shouldBe before500 + 1
  }

  it should "echo an incoming X-Request-ID and generate one otherwise" in {
    Get("/ok").addHeader(RawHeader("X-Request-ID", "req-abc")) ~> route ~> check {
      header("X-Request-ID").map(_.value) shouldBe Some("req-abc")
    }
    Get("/ok") ~> route ~> check {
      header("X-Request-ID").map(_.value).getOrElse("") should not be empty
    }
  }

  "eventTypeLabel" should "keep noun.verb event types and bucket anything else" in {
    HealthRoutes.eventTypeLabel("document.created") shouldBe "document.created"
    HealthRoutes.eventTypeLabel("Robert'); DROP TABLE") shouldBe "other"
    HealthRoutes.eventTypeLabel("x".repeat(200)) shouldBe "other"
  }

  "GET /health" should "report degraded (200) after a storage fallback" in {
    val service = AnalyticsService(MetricsRepository(PostgresConfig("jdbc:postgresql://localhost/x", "u", "p", 2)))
    Get("/health") ~> HealthRoutes(service, storageFallback = true).routes ~> check {
      status shouldBe StatusCodes.OK
      responseAs[String] should include("\"status\":\"degraded\"")
    }
    Get("/health") ~> HealthRoutes(service).routes ~> check {
      responseAs[String] should include("\"status\":\"healthy\"")
    }
  }
