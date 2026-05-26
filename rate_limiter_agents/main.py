from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import logging  # noqa: E402

from apscheduler.schedulers.background import BackgroundScheduler  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from sqlalchemy import text  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

from . import config  # noqa: E402
from .database import agent_engine  # noqa: E402
from .tools.mcp_client import get_mcp  # noqa: E402
from .logging_config import request_id_var, setup_logging  # noqa: E402
from .routers import agents as agents_router  # noqa: E402
from .routers import dashboard as dashboard_router  # noqa: E402
from .routers import evals as evals_router  # noqa: E402
from .scheduler import run_all_agents, run_daily_evals  # noqa: E402

setup_logging()

app = FastAPI(title="Rate Limiter Agents", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_CSP = (
    "default-src 'self'; "
    # Tailwind CDN injects <style> elements at runtime — unsafe-inline required
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' https://static.cloudflareinsights.com; "
    # Chart.js generates data-URI images for canvas export
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none';"
)


@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request_id_var.set(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


app.include_router(agents_router.router, prefix="/agents", tags=["agents"])
app.include_router(dashboard_router.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(evals_router.router, prefix="/evals", tags=["evals"])

_static = Path(__file__).parent / "static"

_scheduler = BackgroundScheduler()
_scheduler.add_job(
    run_all_agents,
    "interval",
    minutes=config.AGENT_INTERVAL_MINUTES,
)
_scheduler.add_job(
    run_daily_evals,
    "cron",
    hour=0,
    minute=0,
    id="daily_evals",
)


app.mount("/static", StaticFiles(directory=_static), name="static")

_index_html = _static / "index.html"


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
async def serve_ui():
    return FileResponse(_index_html)


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok"}


@app.get("/health/ready", tags=["ops"])
async def health_ready():
    checks: dict[str, str] = {}

    try:
        with agent_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["agent_db"] = "ok"
    except Exception as exc:
        logging.error("DB health check failed for agent_db: %s", exc)
        checks["agent_db"] = f"error: {exc}"

    mcp = get_mcp()
    if mcp:
        try:
            # asyncio.to_thread: MCPClient._run calls asyncio.run() internally,
            # which cannot be called from a running event loop directly.
            health = await asyncio.to_thread(mcp.get_service_health)
            checks["mcp"] = health.get("status", "unknown")
        except Exception as exc:
            logging.error("MCP health check failed: %s", exc)
            checks["mcp"] = f"error: {exc}"

    status = (
        "ok" if all(v in ("ok", "healthy") for v in checks.values()) else "degraded"
    )
    code = 200 if status == "ok" else 503
    return JSONResponse(status_code=code, content={"status": status, "checks": checks})


@app.on_event("startup")
async def _startup():
    logging.info("Running Alembic migrations...")
    try:
        alembic_cfg = AlembicConfig(Path(__file__).parent.parent / "alembic.ini")
        alembic_command.upgrade(alembic_cfg, "head")
    except Exception:
        logging.exception("Alembic migration failed — aborting startup")
        raise
    logging.info("Migrations complete")
    _scheduler.start()
    logging.info(
        "Scheduler started — agents run every %s min", config.AGENT_INTERVAL_MINUTES
    )


@app.on_event("shutdown")
async def _shutdown():
    _scheduler.shutdown(wait=True)
