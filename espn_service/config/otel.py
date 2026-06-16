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

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)

_configured = False


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
