package com.otterworks.analytics.observability

import akka.http.scaladsl.model.{ContentTypes, HttpEntity, HttpRequest, HttpResponse, StatusCodes}
import akka.http.scaladsl.model.headers.RawHeader
import akka.http.scaladsl.server.Directives.*
import akka.http.scaladsl.server.{ExceptionHandler, RejectionHandler, Route}
import com.otterworks.analytics.api.HealthRoutes
import io.opentelemetry.api.OpenTelemetry
import io.opentelemetry.api.trace.{Span, SpanKind, StatusCode}
import io.opentelemetry.context.Context
import io.opentelemetry.context.propagation.TextMapGetter
import io.opentelemetry.sdk.autoconfigure.AutoConfiguredOpenTelemetrySdk
import org.slf4j.{LoggerFactory, MDC}

import java.lang.{Iterable as JIterable}
import java.util.UUID
import scala.jdk.CollectionConverters.*

/** Tracing, request IDs, access logging, HTTP metrics and a JSON 500 for the HTTP server. */
object Observability:
  val RequestIdHeader = "X-Request-ID"

  private val logger = LoggerFactory.getLogger("com.otterworks.analytics.http")
  private val ValidRequestId = "^[A-Za-z0-9._:-]{1,128}$".r
  private val StaticSegment = "^(v[0-9]{1,2}|[a-z][a-z_-]{0,31})$".r
  private val ProbePaths = Set("/health", "/metrics")

  def resolveRequestId(candidate: Option[String]): String =
    candidate.filter(ValidRequestId.matches).getOrElse(UUID.randomUUID().toString)

  /** Bounded metric label: word and `v1`-style segments are kept; IDs and other values become `:id`. */
  def routeLabel(path: String): String =
    path.split('/').filter(_.nonEmpty).map(s => if StaticSegment.matches(s) then s else ":id")
      .mkString("/", "/", "")

  /**
   * OpenTelemetry from the standard OTEL_* env vars (service `analytics-service`, OTLP/HTTP).
   * Spans are only exported when OTEL_EXPORTER_OTLP(_TRACES)_ENDPOINT is set; TRACING_ENABLED=false
   * disables tracing entirely.
   */
  def initOpenTelemetry(env: Map[String, String] = sys.env): OpenTelemetry =
    if env.get("TRACING_ENABLED").exists(_.equalsIgnoreCase("false")) then OpenTelemetry.noop()
    else
      val hasEndpoint = env.contains("OTEL_EXPORTER_OTLP_ENDPOINT") ||
        env.contains("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
      val defaults = Map(
        "otel.service.name" -> "analytics-service",
        "otel.exporter.otlp.protocol" -> "http/protobuf",
        "otel.metrics.exporter" -> "none",
        "otel.logs.exporter" -> "none",
      ) ++ (if hasEndpoint then Map.empty else Map("otel.traces.exporter" -> "none"))
      AutoConfiguredOpenTelemetrySdk.builder()
        .addPropertiesSupplier(() => defaults.asJava)
        .setResultAsGlobal()
        .build()
        .getOpenTelemetrySdk

  private object HeaderGetter extends TextMapGetter[HttpRequest]:
    override def keys(carrier: HttpRequest): JIterable[String] =
      carrier.headers.map(_.lowercaseName).asJava
    override def get(carrier: HttpRequest, key: String): String =
      carrier.headers.find(_.is(key.toLowerCase)).map(_.value).orNull

  private def errorHandler(requestId: String, span: Span): ExceptionHandler = ExceptionHandler {
    case ex: Throwable =>
      extractRequest { req =>
        span.recordException(ex)
        span.setStatus(StatusCode.ERROR)
        withMdc(requestId, span) {
          logger.error(s"unhandled_request_error method=${req.method.value} path=${req.uri.path}", ex)
        }
        complete(HttpResponse(
          StatusCodes.InternalServerError,
          entity = HttpEntity(
            ContentTypes.`application/json`,
            s"""{"error":"Internal server error","requestId":"$requestId"}"""),
        ))
      }
  }

  private def withMdc(requestId: String, span: Span)(body: => Unit): Unit =
    val ctx = span.getSpanContext
    MDC.put("requestId", requestId)
    if ctx.isValid then
      MDC.put("traceId", ctx.getTraceId)
      MDC.put("spanId", ctx.getSpanId)
    try body
    finally
      MDC.remove("requestId")
      MDC.remove("traceId")
      MDC.remove("spanId")

  /** Wraps the whole route tree; rejections are resolved inside so 4xx responses are observed. */
  def instrument(openTelemetry: OpenTelemetry)(inner: Route): Route =
    val tracer = openTelemetry.getTracer("com.otterworks.analytics")
    val propagator = openTelemetry.getPropagators.getTextMapPropagator
    extractRequest { req =>
      val requestId = resolveRequestId(req.headers.find(_.is("x-request-id")).map(_.value))
      val path = req.uri.path.toString
      val route = routeLabel(path)
      val method = req.method.value
      val probe = ProbePaths.contains(path)
      val parent = propagator.extract(Context.root(), req, HeaderGetter)
      val span =
        if probe then Span.getInvalid
        else
          tracer.spanBuilder(s"$method $route").setParent(parent).setSpanKind(SpanKind.SERVER)
            .setAttribute("http.request.method", method)
            .setAttribute("url.path", path)
            .setAttribute("http.route", route)
            .setAttribute("request.id", requestId)
            .startSpan()
      val started = System.nanoTime()
      HealthRoutes.activeConnections.inc()

      mapResponse { response =>
        val status = response.status.intValue
        val seconds = (System.nanoTime() - started) / 1e9
        HealthRoutes.activeConnections.dec()
        HealthRoutes.requestDuration.labels(method, route, status.toString).observe(seconds)
        if !probe then
          span.setAttribute("http.response.status_code", status.toLong)
          if status >= 500 then span.setStatus(StatusCode.ERROR)
          withMdc(requestId, span) {
            logger.info(
              s"http_request method=$method path=$path route=$route status=$status " +
                s"durationMs=${math.round(seconds * 100000) / 100.0}")
          }
          span.end()
        response.addHeader(RawHeader(RequestIdHeader, requestId))
      } {
        handleExceptions(errorHandler(requestId, span)) {
          handleRejections(RejectionHandler.default)(inner)
        }
      }
    }
