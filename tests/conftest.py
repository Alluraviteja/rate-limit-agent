import os

# Set env vars before any rate_limiter_agents imports so module-level
# singletons (get_provider(), create_engine()) use safe test values.
os.environ.setdefault("AGENT_DB_URL", "sqlite:///:memory:")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("LLM_PROVIDER", "anthropic")
os.environ.setdefault("LLM_MODEL", "claude-haiku-4-5-20251001")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:8000")
os.environ.setdefault("AGENT_INTERVAL_MINUTES", "15")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects.sqlite import base as _sqlite_base  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from rate_limiter_agents.database import AgentBase, get_agent_db  # noqa: E402

# SQLite only auto-generates PK values for INTEGER PRIMARY KEY, not BIGINT PRIMARY KEY.
# Patch the SQLite type compiler so all BIGINT columns render as INTEGER in tests.
_sqlite_base.SQLiteTypeCompiler.visit_BIGINT = lambda self, type_, **kw: "INTEGER"


@pytest.fixture(scope="session")
def agent_engine():
    # StaticPool ensures all checkouts reuse the same connection, so every
    # session sees the same in-memory database (required for SQLite :memory:).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AgentBase.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def agent_db(agent_engine):
    Session = sessionmaker(bind=agent_engine)
    db = Session()
    yield db
    db.rollback()
    db.close()


@pytest.fixture
def test_client(agent_db):
    from rate_limiter_agents.main import app

    app.dependency_overrides[get_agent_db] = lambda: agent_db

    # No context manager — skips lifespan events (migrations, scheduler start)
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()
