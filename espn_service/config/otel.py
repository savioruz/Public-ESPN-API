"""OpenTelemetry tracing setup for the ESPN service.

Traces only, mirroring the Sheka backend (`backend/src/infras/otel/otel.ts`):
OTLP gRPC (insecure) by default with an HTTP fallback, batched/non-blocking
export, and a noop path when disabled.

Enabled iff `settings.OTEL_ENDPOINT` is non-empty — there is no separate enable
flag. The exported service name is ``{APP_NAME}_{service_type}_{ENVIRONMENT}``
(e.g. ``espn_web_production``); `service_type` is supplied by each entry point
(web / worker / beat) so the three processes show up distinctly in the collector.

`configure_otel()` is idempotent and MUST be called *after* any fork (per
gunicorn worker, per Celery prefork child / beat process) — a TracerProvider
created in a parent process does not survive `fork()`.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)

_configured = False

F = TypeVar("F", bound=Callable[..., Any])


def _tracer():
    """Tracer for hand-written spans. When OTel is disabled this is the API's
    no-op tracer, so the decorator/context managers below add ~zero overhead and
    are always safe to leave in place."""
    from opentelemetry import trace

    return trace.get_tracer("espn_service")


def traced(layer: str | None = None, name: str | None = None) -> Callable[[F], F]:
    """Wrap a sync callable in a span named ``{layer}.{qualname}`` (or ``name``).

    Gives the architectural middle layers (handler / service / client) their own
    spans so traces form a real tree instead of a flat root. Records and re-raises
    exceptions, marking the span as errored.
    """

    def decorator(func: F) -> F:
        span_name = name or (f"{layer}.{func.__qualname__}" if layer else func.__qualname__)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from opentelemetry.trace import SpanKind, StatusCode

            with _tracer().start_as_current_span(span_name, kind=SpanKind.INTERNAL) as span:
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, str(exc))
                    raise

        return wrapper  # type: ignore[return-value]

    return decorator


def set_attrs(**attrs: Any) -> None:
    """Set attributes on the current span (no-op if none active / OTel disabled)."""
    from opentelemetry import trace

    span = trace.get_current_span()
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key, value)


@contextmanager
def detached_context() -> Iterator[None]:
    """Run the block with an empty OTel context (no active span), so a Celery task
    dispatched inside starts its own NEW root trace instead of being linked to the
    current one. Used around fan-out ``.delay()`` calls for per-leaf-task traces."""
    from opentelemetry import context as otel_context

    token = otel_context.attach(otel_context.Context())
    try:
        yield
    finally:
        otel_context.detach(token)


def configure_otel(service_type: str) -> None:
    """Initialise tracing for this process. No-op if disabled or already done."""
    global _configured

    endpoint = (getattr(settings, "OTEL_ENDPOINT", "") or "").strip()
    if _configured or not endpoint:
        return

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    protocol = (getattr(settings, "OTEL_PROTOCOL", "grpc") or "grpc").lower()
    app_name = getattr(settings, "APP_NAME", "espn")
    environment = getattr(settings, "ENVIRONMENT", "development")
    service_name = f"{app_name}_{service_type}_{environment}"

    if protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        url = endpoint if endpoint.startswith(("http://", "https://")) else f"http://{endpoint}"
        exporter = OTLPSpanExporter(endpoint=url, insecure=True)
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        base = endpoint if endpoint.startswith(("http://", "https://")) else f"http://{endpoint}"
        exporter = OTLPSpanExporter(endpoint=f"{base}/v1/traces")

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )
    # Batched, asynchronous export — never blocks request/task handling.
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _instrument()

    _configured = True
    logger.info(
        "otel_initialized",
        service=service_name,
        protocol=protocol,
        endpoint=endpoint,
    )


def _instrument() -> None:
    """Attach auto-instrumentation for the libraries this service uses."""
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    DjangoInstrumentor().instrument()
    CeleryInstrumentor().instrument()
    PsycopgInstrumentor().instrument()
    # Traces every ESPN + relay request made by clients/espn_client.py.
    HTTPXClientInstrumentor().instrument()
