import os
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.engine import Engine


@dataclass
class TelemetryProviders:
    trace_provider: TracerProvider | None
    meter_provider: MeterProvider | None

    def shutdown(self) -> None:
        if self.trace_provider is not None:
            self.trace_provider.force_flush(timeout_millis=5000)
            self.trace_provider.shutdown()
        if self.meter_provider is not None:
            self.meter_provider.force_flush(timeout_millis=5000)
            self.meter_provider.shutdown()


def _enabled_signals(environ: dict[str, str]) -> tuple[bool, bool]:
    generic_endpoint = environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    traces_endpoint = environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    metrics_endpoint = environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "").strip()
    return bool(generic_endpoint or traces_endpoint), bool(generic_endpoint or metrics_endpoint)


def configure_telemetry(
    service_name: str,
    *,
    app=None,
    engine: Engine | None = None,
) -> TelemetryProviders | None:
    """Enable only OTLP signals with an explicitly configured endpoint."""
    trace_enabled, metrics_enabled = _enabled_signals(dict(os.environ))
    if not trace_enabled and not metrics_enabled:
        return None

    resource = Resource.create({"service.name": service_name})
    trace_provider = None
    meter_provider = None
    if trace_enabled:
        trace_provider = TracerProvider(resource=resource)
        trace_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
        trace_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=trace_endpoint or None))
        )
        trace.set_tracer_provider(trace_provider)

    if metrics_enabled:
        metrics_endpoint = os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "").strip()
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=metrics_endpoint or None)
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)

    if app is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=trace_provider,
            meter_provider=meter_provider,
            excluded_urls=(
                r"/api/v1/health/(live|ready),/api/v1/system/status,"
                r"/api/v1/auth/(login|callback)"
            ),
        )
    if engine is not None and (trace_provider is not None or meter_provider is not None):
        SQLAlchemyInstrumentor().instrument(
            engine=engine,
            tracer_provider=trace_provider,
            meter_provider=meter_provider,
        )
    if trace_provider is not None:
        HTTPXClientInstrumentor().instrument(tracer_provider=trace_provider)
    return TelemetryProviders(trace_provider, meter_provider)
