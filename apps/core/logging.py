import logging
import json
import sys
from datetime import datetime, timezone
from contextvars import ContextVar

from opentelemetry import trace

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class JSONFormatter(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:
        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get("-"),
            "trace_id": format(ctx.trace_id, "032x") if ctx and ctx.trace_id else "-",
            "span_id": format(ctx.span_id, "016x") if ctx and ctx.span_id else "-",
        }

        if hasattr(record, "extra_data"):
            log_entry["extra"] = record.extra_data

        return json.dumps(log_entry)


def setup_logging(log_level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger("aegis")
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"aegis.{name}")
