from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

from . import config

# ── Agent DB (read-write: agent_results, orchestrator_results, baseline_memory) ──
agent_engine = create_engine(config.AGENT_DB_URL, pool_pre_ping=True)
AgentSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=agent_engine)
AgentScopedSession = scoped_session(AgentSessionLocal)
AgentBase = declarative_base()


def get_agent_db():
    db = AgentSessionLocal()
    try:
        yield db
    finally:
        db.close()
