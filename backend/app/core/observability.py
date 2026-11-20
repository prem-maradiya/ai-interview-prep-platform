"""Operational plumbing: request IDs, structured access logs, Prometheus metrics and error tracking.

- Every HTTP request and WebSocket gets an ID (from a trusted `X-Request-ID` header, or a new one).
  It is returned in the response header and included in every log line written while handling it,
  so one user report ("request ID abc123 failed") leads straight to the right logs.
- LOG_FORMAT=json writes one JSON object per line (for Loki, CloudWatch, Datadog...); "text" is
  for humans. Access logs never include query strings, bodies or tokens.
- /api/metrics serves Prometheus metrics. Protect it with METRICS_TOKEN; without a token it only
  answers clients on a private network (e.g. Prometheus in the same Docker network).
- SENTRY_DSN enables error reporting (no request bodies, cookies or user emails are sent).
"""
import contextvars
import ipaddress
import json
import logging
import re
import time
import uuid

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app.core.config import get_settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
access_log = logging.getLogger("prepforge.access")

# ---------- metrics ----------
HTTP_REQUESTS = Counter("prepforge_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("prepforge_http_request_duration_seconds", "HTTP request latency", ["method", "route"],
                         buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60))
SANDBOX_RUNS = Counter("prepforge_sandbox_runs_total", "Judged code runs", ["language", "verdict", "kind"])
SANDBOX_LATENCY = Histogram("prepforge_sandbox_run_seconds", "Time to run and judge code", ["language"],
                            buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120))
EMAILS = Counter("prepforge_emails_total", "Emails handed to the email backend", ["backend", "outcome"])
WEBSOCKETS = Gauge("prepforge_websocket_connections", "Open WebSocket connections (this process)")


# ---------- logging ----------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
               "level": record.levelname.lower(), "logger": record.name, "message": record.getMessage(),
               "request_id": getattr(record, "request_id", request_id_var.get())}
        out.update(getattr(record, "fields", {}))
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rid = request_id_var.get()
        prefix = f"{self.formatTime(record, '%H:%M:%S')} {record.name}" + (f" [{rid}]" if rid != "-" else "")
        text = f"{prefix}: {record.getMessage()}"
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


def configure_logging() -> None:
    fmt = JsonFormatter() if get_settings().log_format == "json" else TextFormatter()
    fmt.converter = time.gmtime
    root = logging.getLogger("prepforge")
    handler = next((h for h in root.handlers if getattr(h, "_prepforge", False)), None)
    if handler is None:
        handler = logging.StreamHandler()
        handler._prepforge = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    handler.setFormatter(fmt)
    root.setLevel(logging.INFO)
    root.propagate = False


# ---------- request IDs, access log and HTTP metrics (pure ASGI: works with streaming and WebSockets) ----------
def _route_template(scope) -> str:
    """The route pattern (e.g. /api/problems/{problem_id}), so metrics don't get one series per id.

    Routing fills in `endpoint` and `path_params` on the scope; ids in the path are swapped back
    for their parameter names. Unrouted requests (404s) share one label.
    """
    if scope.get("endpoint") is None:
        return "unmatched"
    parts = scope["path"].split("/")
    for name, value in (scope.get("path_params") or {}).items():
        parts = [f"{{{name}}}" if p == str(value) else p for p in parts]
    return "/".join(parts)


class RequestContextMiddleware:
    def __init__(self, app, fastapi_app) -> None:
        self.app = app
        self.fastapi_app = fastapi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        incoming = dict(scope.get("headers") or {}).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _ID_RE.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [(b"x-request-id", rid.encode())]
            await send(message)

        if scope["type"] == "websocket":
            WEBSOCKETS.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            if scope["type"] == "websocket":
                WEBSOCKETS.dec()
            else:
                route = _route_template(scope)
                if route not in ("/api/metrics", "/api/health", "/api/ready"):
                    HTTP_REQUESTS.labels(scope["method"], route, str(status["code"])).inc()
                    HTTP_LATENCY.labels(scope["method"], route).observe(elapsed)
                    access_log.info("%s %s %s %.0fms", scope["method"], scope["path"], status["code"], elapsed * 1000,
                                    extra={"fields": {"method": scope["method"], "path": scope["path"], "route": route,
                                                      "status": status["code"], "duration_ms": round(elapsed * 1000, 1)}})
            request_id_var.reset(token)


# ---------- metrics endpoint access ----------
def metrics_allowed(client_host: str | None, authorization: str | None) -> bool:
    token = get_settings().metrics_token
    if token:
        import hmac

        return hmac.compare_digest(authorization or "", f"Bearer {token}")
    try:
        ip = ipaddress.ip_address(client_host or "")
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


# ---------- error tracking ----------
def configure_sentry() -> bool:
    s = get_settings()
    if not s.sentry_dsn:
        return False
    import sentry_sdk

    def scrub(event, _hint):
        request = event.get("request") or {}
        for key in ("data", "cookies", "query_string"):
            request.pop(key, None)
        headers = request.get("headers") or {}
        for h in list(headers):
            if h.lower() in ("authorization", "cookie", "x-forwarded-for"):
                headers[h] = "[removed]"
        event.setdefault("tags", {})["request_id"] = request_id_var.get()
        return event

    sentry_sdk.init(dsn=s.sentry_dsn, environment=s.environment, release=s.app_version,
                    traces_sample_rate=s.sentry_traces_sample_rate, send_default_pii=False, before_send=scrub)
    return True
