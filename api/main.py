from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from trustline import metrics
from trustline.logging_config import setup_logging

setup_logging()

from api.routes import audit, compliance, correspondents, ingest, search  # noqa: E402

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

app = FastAPI(
    title="Trustline",
    description="Intelligent audit platform for credit origination data — Banco BMG",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def observability_middleware(request: Request, call_next) -> Response:
    rid = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
    _request_id.set(rid)
    t0 = time.perf_counter()
    response = await call_next(request)
    latency_ms = (time.perf_counter() - t0) * 1000

    path = request.url.path
    status = str(response.status_code)
    metrics.inc("http_requests_total", method=request.method, path=path, status=status)
    metrics.observe("http_request_duration_ms", latency_ms, path=path)

    response.headers["X-Request-ID"] = rid
    return response


app.include_router(ingest.router, prefix="/events", tags=["ingestion"])
app.include_router(correspondents.router, prefix="/correspondents", tags=["correspondents"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(compliance.router, prefix="/compliance", tags=["compliance"])
app.include_router(search.router, prefix="/search", tags=["search"])


@app.get("/health", tags=["ops"])
def health() -> dict:
    from trustline.db.mongo import get_db

    components: dict[str, str] = {}
    try:
        get_db().command("ping")
        components["mongo"] = "ok"
    except Exception as exc:
        components["mongo"] = f"error: {exc}"

    return {"status": "ok" if all(v == "ok" for v in components.values()) else "degraded",
            "components": components}


@app.get("/metrics", tags=["ops"])
def prometheus_metrics() -> Response:
    return Response(content=metrics.prometheus_text(), media_type="text/plain")
