"""Arize AX tracing via OpenInference. Env-gated — no-op without ARIZE_* keys.

We build the tracer provider directly (HTTP/protobuf exporter) instead of
using arize-otel's register(), which has a bug where the Endpoint enum
isn't unwrapped before being passed to the HTTP exporter.
"""

import os

ARIZE_HTTP_ENDPOINT = "https://otlp.arize.com/v1/traces"


def init_tracing():
    """Initialize OpenInference instrumentation exporting to Arize AX over HTTP.

    No-op (and returns False) if ARIZE_SPACE_ID or ARIZE_API_KEY is missing.
    Returns True when tracing is active.
    """
    space_id = os.environ.get("ARIZE_SPACE_ID")
    api_key = os.environ.get("ARIZE_API_KEY")
    if not space_id or not api_key:
        return False

    try:
        from opentelemetry.sdk import trace as trace_sdk
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry import trace as otel_trace
        from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
    except ImportError as e:
        print(f"[tracing] Arize libraries not installed ({e}); skipping.")
        return False

    project_name = os.environ.get("ARIZE_PROJECT_NAME", "drone-show-manager")

    resource = Resource.create({
        "model_id": project_name,
        "service.name": project_name,
    })

    exporter = OTLPSpanExporter(
        endpoint=ARIZE_HTTP_ENDPOINT,
        headers={
            "api_key": api_key,
            "space_id": space_id,
            "arize-space-id": space_id,
            "authorization": api_key,
        },
    )

    tracer_provider = trace_sdk.TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    otel_trace.set_tracer_provider(tracer_provider)

    OpenAIAgentsInstrumentor().instrument(tracer_provider=tracer_provider)

    print(
        f"[tracing] Arize AX active — project '{project_name}' "
        f"→ {ARIZE_HTTP_ENDPOINT}"
    )
    return True
