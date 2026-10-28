# LEGACY: Uses JDK 8 (target: JDK 17+ or 21+)
# Maven build instead of Gradle (matches legacy Java enterprise pattern)
FROM maven:3.8.7-eclipse-temurin-8 AS builder

WORKDIR /app
COPY pom.xml .
# Download dependencies first for Docker layer caching
RUN mvn dependency:go-offline -B

COPY src/ src/
RUN mvn package -DskipTests -B

# LEGACY: JRE 8 runtime (target: eclipse-temurin:17-jre or 21-jre)
FROM eclipse-temurin:8-jre

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# OpenTelemetry Java agent: HTTP server/client and JDBC spans, plus trace_id/span_id
# in the logging MDC. Off unless OTEL_SDK_DISABLED=false and an OTLP endpoint are
# provided (docker-compose sets both).
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

RUN mkdir -p /tmp/reports

COPY --from=builder /app/target/report-service.jar app.jar

RUN useradd -r -u 1001 appuser && chown appuser:appuser /tmp/reports
USER appuser

EXPOSE 8091

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8091/health || exit 1

ENTRYPOINT ["java", "-jar", "app.jar"]
