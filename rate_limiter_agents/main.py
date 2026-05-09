from __future__ import annotations

import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from . import config
from .database import agent_engine, rate_limiter_engine
from .logging_config import request_id_var, setup_logging
from .routers import agents as agents_router
from .routers import dashboard as dashboard_router
from .routers import evals as evals_router
from .scheduler import run_all_agents, run_daily_evals

setup_logging()

app = FastAPI(title="Rate Limiter Agents", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request_id_var.set(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response

app.include_router(agents_router.router, prefix="/agents", tags=["agents"])
app.include_router(dashboard_router.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(evals_router.router, prefix="/evals", tags=["evals"])

_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static)), name="static")

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


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok"}


@app.get("/health/ready", tags=["ops"])
async def health_ready():
    checks: dict[str, str] = {}

    for name, engine in (("rate_limiter_db", rate_limiter_engine), ("agent_db", agent_engine)):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks[name] = "ok"
        except Exception as exc:
            logging.error("DB health check failed for %s: %s", name, exc)
            checks[name] = f"error: {exc}"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    code = 200 if status == "ok" else 503
    return JSONResponse(status_code=code, content={"status": status, "checks": checks})


@app.on_event("startup")
async def _startup():
    logging.info("Running Alembic migrations...")
    alembic_cfg = AlembicConfig(Path(__file__).parent.parent / "alembic.ini")
    alembic_command.upgrade(alembic_cfg, "head")
    logging.info("Migrations complete")
    _scheduler.start()
    logging.info("Scheduler started — agents run every %s min", config.AGENT_INTERVAL_MINUTES)


@app.on_event("shutdown")
async def _shutdown():
    _scheduler.shutdown(wait=True)
