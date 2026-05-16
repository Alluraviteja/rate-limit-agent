from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_agent_db
from ..models import AgentResult
from ..scheduler import execute_agent_pipeline
from ..tools.mcp_client import get_mcp
from .. import schemas

router = APIRouter()


def _to_dict(obj) -> dict:
    result = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name)
        if isinstance(val, Decimal):
            val = float(val)
        elif isinstance(val, datetime):
            val = val.isoformat()
        result[col.name] = val
    name = result.get("agent_name", "")
    if name == "token_bucket_health":
        result["avg_remaining_tokens"] = result.get("baseline_rps")
    elif name == "top_paths":
        result["unique_paths"] = result.get("unique_ips")
    return result


def _enabled_ids(app_info_id: Optional[int]) -> list[int]:
    if app_info_id:
        return [app_info_id]
    mcp = get_mcp()
    if mcp is None:
        return []
    return [int(a["id"]) for a in mcp.list_apps()]


@router.post("/run", response_model=list[schemas.RunResultOut])
def run_agents(
    app_info_id: Optional[int] = Query(None, gt=0),
    agent_db: Session = Depends(get_agent_db),
):
    """Manually trigger agents. Runs for all enabled apps if app_info_id not specified."""
    ids = _enabled_ids(app_info_id)
    results = []
    for aid in ids:
        error, token, paths, orch = execute_agent_pipeline(agent_db, aid)
        results.append(
            {
                "app_info_id": aid,
                "error": _to_dict(error),
                "token": _to_dict(token),
                "paths": _to_dict(paths),
                "orchestrator": _to_dict(orch),
            }
        )
    return results


@router.get("/history", response_model=list[schemas.AgentResultOut])
def get_history(
    app_info_id: Optional[int] = Query(None, gt=0),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    agent_db: Session = Depends(get_agent_db),
):
    ids = _enabled_ids(app_info_id)
    rows = (
        agent_db.query(AgentResult)
        .filter(AgentResult.app_info_id.in_(ids))
        .order_by(AgentResult.run_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_to_dict(r) for r in rows]
