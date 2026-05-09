from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

from . import config

# ── Rate limiter DB (read-only: rate_limit_log, app_info, rate_limit_plan) ──
rate_limiter_engine = create_engine(config.RATE_LIMITER_DB, pool_pre_ping=True)
RateLimiterSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=rate_limiter_engine)
RateLimiterScopedSession = scoped_session(RateLimiterSessionLocal)
RateLimiterBase = declarative_base()

# ── Agent DB (read-write: agent_results, orchestrator_results, baseline_memory) ──
agent_engine = create_engine(config.AGENT_DB_URL, pool_pre_ping=True)
AgentSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=agent_engine)
AgentScopedSession = scoped_session(AgentSessionLocal)
AgentBase = declarative_base()


def get_rate_db():
    db = RateLimiterSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_agent_db():
    db = AgentSessionLocal()
    try:
        yield db
    finally:
        db.close()
