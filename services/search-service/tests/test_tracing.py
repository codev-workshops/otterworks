from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from app import tracing


def test_tracing_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    assert tracing.tracing_enabled() is False
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    assert tracing.tracing_enabled() is True


def test_add_trace_context_inside_span():
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        event = tracing.add_trace_context(None, "info", {"event": "x"})
    ctx = span.get_span_context()
    assert event["trace_id"] == format(ctx.trace_id, "032x")
    assert event["span_id"] == format(ctx.span_id, "016x")


def test_add_trace_context_without_span():
    assert tracing.add_trace_context(None, "info", {"event": "x"}) == {"event": "x"}
    assert trace.get_current_span().get_span_context().is_valid is False
