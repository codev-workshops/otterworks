FROM sbtscala/scala-sbt:eclipse-temurin-jammy-17.0.10_7_1.9.9_3.4.1 AS builder

WORKDIR /app
COPY build.sbt ./
COPY project/ ./project/
RUN sbt update || true

COPY src ./src
RUN sbt assembly

FROM eclipse-temurin:17-jre-jammy
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# OpenTelemetry Java agent: Akka HTTP server, JDBC and AWS SDK spans, plus
# trace_id/span_id in the Logback MDC. Off unless OTEL_SDK_DISABLED=false and
# an OTLP endpoint are provided (docker-compose sets both).
ARG OTEL_JAVAAGENT_VERSION=2.10.0
ARG OTEL_JAVAAGENT_SHA256=d05f6e36fac8db629263a6aaec2841cc934d064d7b19bfe38425b604b8b54926
RUN curl -fsSL -o /opt/opentelemetry-javaagent.jar \
      "https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_JAVAAGENT_VERSION}/opentelemetry-javaagent.jar" \
    && echo "${OTEL_JAVAAGENT_SHA256}  /opt/opentelemetry-javaagent.jar" | sha256sum -c -
ENV JAVA_TOOL_OPTIONS="-javaagent:/opt/opentelemetry-javaagent.jar" \
    OTEL_SDK_DISABLED=true \
    OTEL_TRACES_EXPORTER=otlp \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none

WORKDIR /app
COPY --from=builder /app/target/scala-3.4.0/analytics-service-assembly-*.jar app.jar

RUN useradd -r -u 1001 appuser
USER appuser

EXPOSE 8088
HEALTHCHECK --interval=10s --timeout=3s CMD curl -f http://localhost:8088/health || exit 1
ENTRYPOINT ["java", "-jar", "app.jar"]
