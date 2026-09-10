"""Structured logging, request correlation, and Prometheus metrics.

Before this the only signals were uvicorn's plain-text access lines and a JSON
/api/metrics endpoint that counts rows. Neither answers the questions this app
has actually needed answered:

  * did Yahoo just start dropping symbols from batches?      (it did, twice)
  * is the model quota exhausted, or is the agent broken?    (looked identical)
  * is the cache doing anything, or is every worker refetching?
  * did a guardrail fire, and which one?
  * did that alert not arrive because nothing happened, or because the limiter
    suppressed it?

So the metrics here are deliberately domain-shaped rather than a generic HTTP
dashboard. Every counter below exists because a real incident in this codebase
was hard to diagnose without it.

Logs are JSON on stdout — nothing here knows about Elasticsearch. The container
runtime captures stdout and Filebeat ships it, which keeps the app free of any
coupling to the log stack and means `docker compose logs` still works when that
stack is not running.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import Request, Response

SERVICE = os.environ.get("SERVICE_NAME", "adviser-backend")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Correlation id, set per request and readable from anywhere without threading
# it through every function signature.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    """One JSON object per line — the shape Filebeat and Elasticsearch expect."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "@timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                          + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            # ECS shape: Elasticsearch's beats template maps `service` and
            # `error` as OBJECTS. Emitting them as bare strings makes every
            # document fail with document_parsing_exception and Filebeat drops
            # the lot silently — the app looks fine, the logs simply never
            # arrive. Nesting them costs nothing and matches the schema.
            "service": {"name": SERVICE},
            "pid": record.process,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        # Anything passed via logger.info("...", extra={"foo": 1}) is promoted to
        # a top-level field so it is queryable rather than buried in the message.
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["error"] = {"message": str(record.exc_info[1]),
                                "type": record.exc_info[0].__name__,
                                "stack_trace": self.formatException(record.exc_info)}
        return json.dumps(payload, default=str)


def log(name: str = "app") -> logging.Logger:
    return logging.getLogger(name)


def event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Log with structured fields: event(log(), INFO, 'msg', symbol='INFY.NS')."""
    logger.log(level, message, extra={"extra_fields": fields})


def setup_logging() -> None:
    """JSON to stdout, and uvicorn's own loggers folded into the same format."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(LOG_LEVEL)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False
    # uvicorn's access line duplicates our own request log with less detail.
    logging.getLogger("uvicorn.access").disabled = True


# --------------------------------------------------------------- metrics

_PROM = None


def _prom():
    """Prometheus client, or None. Metrics must never be a hard dependency."""
    global _PROM
    if _PROM is not None:
        return _PROM or None
    try:
        from prometheus_client import (
            CollectorRegistry, Counter, Gauge, Histogram, multiprocess,
        )

        # With uvicorn --workers each worker is a separate process, so counters
        # must live in a shared directory or /metrics would only ever report
        # whichever worker happened to answer the scrape.
        if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
        else:
            from prometheus_client import REGISTRY as registry  # type: ignore

        _PROM = {
            "registry": registry,
            "requests": Counter("adviser_http_requests_total",
                                "HTTP requests", ["method", "route", "status"]),
            "latency": Histogram("adviser_http_request_seconds",
                                 "Request duration", ["route"],
                                 buckets=(.01, .05, .1, .5, 1, 5, 15, 60, 300)),
            "yahoo": Counter("adviser_yahoo_fetch_total",
                             "Yahoo fetch outcomes", ["kind", "outcome"]),
            "cache": Counter("adviser_cache_total",
                             "Cache lookups", ["namespace", "result"]),
            "agent": Counter("adviser_agent_runs_total",
                             "Agent runs", ["agent", "outcome"]),
            "guardrail": Counter("adviser_guardrail_violations_total",
                                 "Guardrail hits", ["kind", "severity"]),
            "ratelimit": Counter("adviser_rate_limit_rejections_total",
                                 "Throttled requests", ["tier"]),
            "alerts": Counter("adviser_gold_alerts_total",
                              "Gold alert outcomes", ["outcome"]),
        }
    except Exception:
        _PROM = {}
    return _PROM or None


def metric(name: str, labels: Optional[dict] = None, observe: Optional[float] = None) -> None:
    """Record one metric. Silently does nothing if prometheus_client is absent."""
    p = _prom()
    if not p or name not in p:
        return
    try:
        m = p[name].labels(**labels) if labels else p[name]
        if observe is not None:
            m.observe(observe)
        else:
            m.inc()
    except Exception:
        pass   # instrumentation must never break the thing it measures


def mark_worker_dead(pid: int) -> None:
    """Drop a dead worker's counter files.

    Without this /tmp/prom accumulates one file set per worker per restart, and
    gauges from processes that no longer exist keep being reported.
    """
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(pid)
    except Exception:
        pass


def exposition() -> tuple[bytes, str]:
    """The /metrics payload for Prometheus to scrape."""
    p = _prom()
    if not p:
        return (b"# prometheus_client not installed\n", "text/plain")
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return generate_latest(p["registry"]), CONTENT_TYPE_LATEST


# ------------------------------------------------------------ middleware

def _route_of(request: Request) -> str:
    """Templated path (/api/stocks/{symbol}) not the literal one.

    Using the raw path would create a distinct metric series per symbol, which
    is the classic way to blow up a Prometheus instance with unbounded
    cardinality.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


async def middleware(request: Request, call_next: Callable) -> Response:
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    started = time.perf_counter()
    logger = log("http")
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["x-request-id"] = rid
        return response
    finally:
        elapsed = time.perf_counter() - started
        route = _route_of(request)
        metric("requests", {"method": request.method, "route": route, "status": str(status)})
        metric("latency", {"route": route}, observe=elapsed)
        if status == 429:
            metric("ratelimit", {"tier": "app"})
        # Health checks every 20s would drown everything else.
        if request.url.path not in ("/api/health", "/api/ready", "/metrics"):
            event(logger, logging.WARNING if status >= 500 else logging.INFO,
                  f"{request.method} {request.url.path} {status}",
                  http_method=request.method, http_route=route,
                  http_path=request.url.path, http_status=status,
                  duration_ms=round(elapsed * 1000, 1),
                  client_ip=request.client.host if request.client else None)
        request_id_var.reset(token)
