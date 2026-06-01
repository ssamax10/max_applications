from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider


def configure_tracing(service_name: str) -> None:
    trace.set_tracer_provider(
        TracerProvider(resource=Resource.create({"service.name": service_name}))
    )
