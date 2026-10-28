package com.otterworks.analytics.api

import akka.http.scaladsl.model.StatusCodes
import akka.http.scaladsl.model.headers.RawHeader
import akka.http.scaladsl.server.Directives.*
import akka.http.scaladsl.server.{ExceptionHandler, RejectionHandler, Route, RouteResult}
import io.prometheus.client.{Counter, Gauge, Histogram}
import net.logstash.logback.argument.StructuredArguments.kv
import org.slf4j.LoggerFactory

import java.util.UUID
import scala.concurrent.ExecutionContext
import scala.util.control.NonFatal
import scala.util.{Failure, Success}

/**
 * Request instrumentation shared by every route: the fleet-wide
 * `http_requests_total` / `http_request_duration_seconds` metrics, an
 * in-flight gauge, `X-Request-ID` propagation and one JSON access log line
 * per request (scrape and probe endpoints are not logged).
 */
object HttpMetrics:

  val RequestIdHeader = "X-Request-ID"

  val requestsTotal: Counter = Counter.build()
    .name("http_requests_total")
    .help("Total HTTP requests")
    .labelNames("method", "path", "status")
    .register()

  val requestDuration: Histogram = Histogram.build()
    .name("http_request_duration_seconds")
    .help("HTTP request duration in seconds")
    .labelNames("method", "path", "status")
    .buckets(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
    .register()

  val activeConnections: Gauge = Gauge.build()
    .name("analytics_active_connections")
    .help("Number of in-flight HTTP requests")
    .register()

  private val logger = LoggerFactory.getLogger("com.otterworks.analytics.access")

  private val unloggedPaths = Set("/health", "/metrics")

  private val templates: Seq[(scala.util.matching.Regex, String)] = Seq(
    "^/api/v1/analytics/users/[^/]+/activity$".r -> "/api/v1/analytics/users/:id/activity",
    "^/api/v1/analytics/documents/[^/]+/stats$".r -> "/api/v1/analytics/documents/:id/stats",
  )

  /** Route template for the `path` label; unmatched paths collapse to one value. */
  def pathLabel(path: String, status: Int): String =
    templates.collectFirst { case (re, t) if re.matches(path) => t }
      .getOrElse(if status == StatusCodes.NotFound.intValue then "unmatched" else path)

  private val errorHandler = ExceptionHandler { case NonFatal(ex) =>
    extractUri { uri =>
      logger.error("Unhandled exception for {}", uri.path, ex)
      complete(StatusCodes.InternalServerError)
    }
  }

  def instrument(inner: Route)(using ec: ExecutionContext): Route =
    val sealedInner =
      handleExceptions(errorHandler) { handleRejections(RejectionHandler.default) { inner } }
    optionalHeaderValueByName(RequestIdHeader) { incoming =>
      val requestId = incoming.filter(_.nonEmpty).getOrElse(UUID.randomUUID().toString)
      respondWithHeader(RawHeader(RequestIdHeader, requestId)) { ctx =>
        val start = System.nanoTime()
        val method = ctx.request.method.value
        val rawPath = ctx.request.uri.path.toString
        activeConnections.inc()
        val result = sealedInner(ctx)
        result.onComplete { outcome =>
          activeConnections.dec()
          val status = outcome match
            case Success(RouteResult.Complete(response)) => response.status.intValue
            case Success(RouteResult.Rejected(_)) => StatusCodes.NotFound.intValue
            case Failure(_) => StatusCodes.InternalServerError.intValue
          record(method, rawPath, status, System.nanoTime() - start, requestId)
        }
        result
      }
    }

  private def record(method: String, rawPath: String, status: Int, nanos: Long, requestId: String): Unit =
    val path = pathLabel(rawPath, status)
    val seconds = nanos / 1e9
    requestsTotal.labels(method, path, status.toString).inc()
    requestDuration.labels(method, path, status.toString).observe(seconds)
    if !unloggedPaths.contains(rawPath) then
      logger.info(
        "{} {} {}",
        kv("method", method),
        kv("path", path),
        kv("status", status),
        kv("duration_ms", math.round(seconds * 1000)),
        kv("request_id", requestId),
      )
