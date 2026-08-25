from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from prometheus_client import make_asgi_app, Counter, Histogram

from apps.api.config import settings

# ─── Custom Business Metrics (Prometheus) ──────────────────────────

INCIDENTS_CREATED = Counter(
    "aegis_incidents_created_total",
    "Total incidents created",
)
INCIDENTS_RESOLVED = Counter(
    "aegis_incidents_resolved_total",
    "Total incidents resolved",
)
FAILURES_INJECTED = Counter(
    "aegis_failures_injected_total",
    "Total failure injections",
    ["failure_type", "service_name"],
)
FAILURES_RESOLVED = Counter(
    "aegis_failures_resolved_total",
    "Total failures resolved",
)
REQUEST_DURATION = Histogram(
    "aegis_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path", "status_code"],
)


def setup_telemetry(app):
    """Initialize all telemetry. MUST be called before engine creation."""

    resource = Resource.create({"service.name": "aegis-api"})

    # 1. Tracing → Jaeger via OTLP
    if settings.OTEL_ENABLED:
        tracer_provider = TracerProvider(resource=resource)
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.OTEL_EXPORTER_ENDPOINT,
            insecure=True,
        )
        tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        trace.set_tracer_provider(tracer_provider)

        # Instrument SQLAlchemy at class level (before any engine exists)
        SQLAlchemyInstrumentor().instrument()

        # Instrument FastAPI
        FastAPIInstrumentor.instrument_app(app)

    # 2. Metrics → Prometheus /metrics endpoint
    if settings.PROMETHEUS_ENABLED:
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)


def shutdown_telemetry():
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
