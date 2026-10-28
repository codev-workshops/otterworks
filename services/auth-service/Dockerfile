FROM gradle:8.6-jdk17 AS builder

WORKDIR /app
COPY build.gradle settings.gradle ./
COPY gradle ./gradle
RUN gradle dependencies --no-daemon || true

COPY src ./src
RUN gradle bootJar --no-daemon

FROM eclipse-temurin:17-jre-jammy
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# OpenTelemetry Java agent: Spring MVC server, JDBC and HTTP client spans, plus
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
COPY --from=builder /app/build/libs/*.jar app.jar

RUN useradd -r -u 1001 appuser
USER appuser

EXPOSE 8081
HEALTHCHECK --interval=15s --timeout=5s CMD curl -f http://localhost:8081/health || exit 1
ENTRYPOINT ["java", "-jar", "app.jar"]
