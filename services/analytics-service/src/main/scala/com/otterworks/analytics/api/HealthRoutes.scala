package com.otterworks.analytics.api

import akka.http.scaladsl.model.{ContentTypes, HttpEntity, StatusCodes}
import akka.http.scaladsl.server.Directives.*
import akka.http.scaladsl.server.Route
import com.otterworks.analytics.service.AnalyticsService
import io.prometheus.client.{CollectorRegistry, Counter, Gauge}
import io.prometheus.client.exporter.common.TextFormat
import spray.json.*

import java.io.StringWriter
import scala.concurrent.ExecutionContext
import scala.util.{Failure, Success}

/**
 * Health and metrics endpoints:
 *   GET /health  - Service health check
 *   GET /metrics - Prometheus metrics
 */
class HealthRoutes(analyticsService: AnalyticsService, storageFallback: Boolean = false)(using ec: ExecutionContext):

  // A fallback to the in-memory store keeps serving (200) but reports
  // "degraded": the probes must not restart the pod, but data is not durable.
  private val okStatus = if storageFallback then "degraded" else "healthy"

  val routes: Route = concat(
    path("health") {
      get {
        onComplete(analyticsService.getEventCount) {
          case Success(count) =>
            complete(HttpEntity(
              ContentTypes.`application/json`,
              s"""{"status":"$okStatus","service":"analytics-service","eventsProcessed":$count,"storageFallback":$storageFallback}"""
            ))
          case Failure(_) =>
            complete(StatusCodes.ServiceUnavailable, HttpEntity(
              ContentTypes.`application/json`,
              """{"status":"degraded","service":"analytics-service","eventsProcessed":0}"""
            ))
        }
      }
    },
    path("metrics") {
      get {
        val writer = new StringWriter()
        TextFormat.write004(writer, CollectorRegistry.defaultRegistry.metricFamilySamples())
        val metricsOutput = writer.toString
        complete(HttpEntity(ContentTypes.`text/plain(UTF-8)`, metricsOutput))
      }
    },
  )

object HealthRoutes:
  private val eventTypePattern = "^[a-z0-9_]{1,32}(\\.[a-z0-9_]{1,32}){0,2}$".r

  /** Bounded `event_type` label: anything outside the `noun.verb` shape is "other". */
  def eventTypeLabel(eventType: String): String =
    if eventTypePattern.matches(eventType) then eventType else "other"

  val eventsReceivedTotal: Counter = Counter.build()
    .name("analytics_events_received_total")
    .help("Total number of analytics events received")
    .labelNames("event_type", "source")
    .register()

  val repositoryBackend: Gauge = Gauge.build()
    .name("analytics_repository_backend")
    .help("Active metrics store backend (1 = active); in-memory after a Postgres fallback")
    .labelNames("backend")
    .register()
