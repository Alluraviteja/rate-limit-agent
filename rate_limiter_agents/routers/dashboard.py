from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_agent_db
from ..models import ActionOutcome, AgentResult, OrchestratorResult, TimeBaseline
from ..tools.memory_service import get_or_create_baseline
from ..tools.mcp_client import get_mcp
from .. import schemas

router = APIRouter()


def _val(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _row(obj) -> dict:
    return {c.name: _val(getattr(obj, c.name)) for c in obj.__table__.columns}


def _enabled_ids(app_info_id: Optional[int], agent_db: Session) -> list[int]:
    if app_info_id:
        return [app_info_id]
    mcp = get_mcp()
    if mcp is not None:
        return [int(a["id"]) for a in mcp.list_apps()]
    rows = agent_db.query(OrchestratorResult.app_info_id).distinct().all()
    return [r[0] for r in rows if r[0] is not None]


@router.get("/apps", response_model=list[schemas.AppOut])
def apps(agent_db: Session = Depends(get_agent_db)):
    mcp = get_mcp()
    if mcp is not None:
        return mcp.list_apps()
    rows = agent_db.query(OrchestratorResult.app_info_id).distinct().all()
    return [
        {"id": r[0], "service_name": f"App {r[0]}", "display_name": f"App {r[0]}"}
        for r in rows
        if r[0] is not None
    ]


@router.get("/status", response_model=list[schemas.AppStatusOut])
def status(agent_db: Session = Depends(get_agent_db)):
    """Latest orchestrator result per app — powers the per-app status grid."""
    ids = _enabled_ids(None, agent_db)
    apps_map: dict[int, str] = {}
    mcp = get_mcp()
    if mcp is not None:
        apps_map = {int(a["id"]): a.get("display_name") or a.get("service_name") or f"App {a['id']}" for a in mcp.list_apps()}

    result = []
    for app_id in ids:
        latest = (
            agent_db.query(OrchestratorResult)
            .filter(OrchestratorResult.app_info_id == app_id)
            .order_by(OrchestratorResult.run_at.desc())
            .first()
        )
        if latest:
            agents_firing = sum(
                1 for s in [latest.error_severity, latest.token_severity, latest.path_severity]
                if s and s != "none"
            )
            result.append({
                "app_info_id": app_id,
                "app_name": apps_map.get(app_id) or f"App {app_id}",
                "final_severity": latest.final_severity,
                "action": latest.action,
                "anomaly_detected": latest.anomaly_detected,
                "spike_multiplier": float(latest.spike_multiplier) if latest.spike_multiplier is not None else None,
                "trend_direction": latest.trend_direction,
                "agents_firing": agents_firing,
                "error_severity": latest.error_severity,
                "token_severity": latest.token_severity,
                "path_severity": latest.path_severity,
                "reason": latest.reason,
                "run_at": latest.run_at.isoformat() if latest.run_at else None,
            })
    return result


@router.get("/summary", response_model=schemas.SummaryOut)
def summary(
    app_info_id: Optional[int] = Query(None, gt=0),
    agent_db: Session = Depends(get_agent_db),
):
    ids = _enabled_ids(app_info_id, agent_db)

    def aq(model):
        return agent_db.query(model).filter(model.app_info_id.in_(ids))

    total_agent_runs = (
        aq(AgentResult).with_entities(func.count(AgentResult.id)).scalar() or 0
    )
    total_orch_runs = (
        aq(OrchestratorResult).with_entities(func.count(OrchestratorResult.id)).scalar()
        or 0
    )
    anomaly_count = (
        aq(OrchestratorResult)
        .filter(OrchestratorResult.anomaly_detected.is_(True))
        .with_entities(func.count(OrchestratorResult.id))
        .scalar()
        or 0
    )
    critical_count = (
        aq(OrchestratorResult)
        .filter(OrchestratorResult.final_severity == "critical")
        .with_entities(func.count(OrchestratorResult.id))
        .scalar()
        or 0
    )
    high_count = (
        aq(OrchestratorResult)
        .filter(OrchestratorResult.final_severity == "high")
        .with_entities(func.count(OrchestratorResult.id))
        .scalar()
        or 0
    )

    agent_tokens = (
        aq(AgentResult).with_entities(func.sum(AgentResult.tokens_used)).scalar() or 0
    )
    orch_tokens = (
        aq(OrchestratorResult)
        .with_entities(func.sum(OrchestratorResult.tokens_used))
        .scalar()
        or 0
    )
    total_tokens = int(agent_tokens) + int(orch_tokens)

    agent_cost = float(
        aq(AgentResult).with_entities(func.sum(AgentResult.cost_usd)).scalar() or 0
    )
    orch_cost = float(
        aq(OrchestratorResult)
        .with_entities(func.sum(OrchestratorResult.cost_usd))
        .scalar()
        or 0
    )
    total_cost = round(agent_cost + orch_cost, 5)

    last = aq(OrchestratorResult).order_by(OrchestratorResult.run_at.desc()).first()
    baselines = [_row(get_or_create_baseline(agent_db, i)) for i in ids]

    return {
        "app_info_ids": ids,
        "total_agent_runs": total_agent_runs,
        "total_orchestrator_runs": total_orch_runs,
        "anomaly_count": anomaly_count,
        "critical_count": critical_count,
        "high_count": high_count,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "last_run_at": last.run_at.isoformat() if last else None,
        "last_severity": last.final_severity if last else "none",
        "baselines": baselines,
    }


@router.get("/timeline", response_model=list[schemas.TimelineItemOut])
def timeline(
    app_info_id: Optional[int] = Query(None, gt=0),
    filter: schemas.TimelineFilter = Query(schemas.TimelineFilter.all),
    agent: schemas.AgentFilter = Query(schemas.AgentFilter.all),
    limit: int = Query(15, ge=1, le=100),
    offset: int = Query(0, ge=0),
    agent_db: Session = Depends(get_agent_db),
):
    ids = _enabled_ids(app_info_id, agent_db)

    q = agent_db.query(OrchestratorResult).filter(
        OrchestratorResult.app_info_id.in_(ids)
    )

    if filter == schemas.TimelineFilter.anomaly:
        q = q.filter(OrchestratorResult.anomaly_detected.is_(True))
    elif filter in (
        schemas.TimelineFilter.critical,
        schemas.TimelineFilter.high,
        schemas.TimelineFilter.medium,
        schemas.TimelineFilter.low,
    ):
        q = q.filter(OrchestratorResult.final_severity == filter.value)

    if agent == schemas.AgentFilter.error:
        q = q.filter(OrchestratorResult.error_severity != "none")
    elif agent == schemas.AgentFilter.token:
        q = q.filter(OrchestratorResult.token_severity != "none")
    elif agent == schemas.AgentFilter.paths:
        q = q.filter(OrchestratorResult.path_severity != "none")
    elif agent == schemas.AgentFilter.orchestrator:
        q = q.filter(OrchestratorResult.final_severity != "none")

    orch_rows = (
        q.order_by(OrchestratorResult.run_at.desc()).offset(offset).limit(limit).all()
    )

    # Batch-fetch outcomes so the timeline doesn't do N+1 queries.
    orch_ids = [o.id for o in orch_rows]
    outcomes_by_orch = {
        o.orchestrator_result_id: o
        for o in agent_db.query(ActionOutcome)
        .filter(ActionOutcome.orchestrator_result_id.in_(orch_ids))
        .all()
    }

    results = []
    for orch in orch_rows:
        window_start = orch.run_at - timedelta(seconds=30)
        window_end = orch.run_at + timedelta(seconds=30)
        agents = (
            agent_db.query(AgentResult)
            .filter(
                AgentResult.app_info_id == orch.app_info_id,
                AgentResult.run_at >= window_start,
                AgentResult.run_at <= window_end,
            )
            .all()
        )
        by_name = {str(a.agent_name): a for a in agents}
        e = by_name.get("error_pattern")
        t = by_name.get("token_bucket_health")
        p = by_name.get("top_paths")
        outcome = outcomes_by_orch.get(orch.id)

        agents_firing = sum(
            1 for s in [orch.error_severity, orch.token_severity, orch.path_severity]
            if s and s != "none"
        )
        results.append(
            {
                "app_info_id": orch.app_info_id,
                "run_at": orch.run_at.isoformat(),
                "final_severity": orch.final_severity,
                "anomaly_detected": orch.anomaly_detected,
                "spike_multiplier": float(orch.spike_multiplier) if orch.spike_multiplier is not None else None,
                "trend_direction": orch.trend_direction,
                "agents_firing": agents_firing,
                "action": orch.action,
                "reason": orch.reason,
                "tokens_used": orch.tokens_used,
                "cost_usd": float(orch.cost_usd or 0),
                "anomaly_resolved": outcome.anomaly_resolved if outcome else None,
                "severity_after": outcome.severity_after if outcome else None,
                "error": {
                    "severity": e.severity if e else "none",
                    "block_rate_pct": float(e.block_rate_pct or 0) if e else 0,
                    "total_requests": e.total_requests if e else 0,
                    "blocked_requests": e.blocked_requests if e else 0,
                    "reason": e.reason if e else "",
                    "action": e.action if e else "monitor",
                    "tokens_used": e.tokens_used if e else None,
                },
                "token": {
                    "severity": t.severity if t else "none",
                    "avg_remaining": float(t.baseline_rps or 0) if t else 0,
                    "total_requests": t.total_requests if t else 0,
                    "reason": t.reason if t else "",
                    "action": t.action if t else "monitor",
                    "tokens_used": t.tokens_used if t else None,
                },
                "paths": {
                    "severity": p.severity if p else "none",
                    "block_rate_pct": float(p.block_rate_pct or 0) if p else 0,
                    "unique_paths": p.unique_ips if p else 0,
                    "total_requests": p.total_requests if p else 0,
                    "reason": p.reason if p else "",
                    "action": p.action if p else "monitor",
                    "tokens_used": p.tokens_used if p else None,
                },
            }
        )
    return results


@router.get("/baseline", response_model=list[schemas.BaselineOut])
def baseline(
    app_info_id: Optional[int] = Query(None, gt=0),
    agent_db: Session = Depends(get_agent_db),
):
    ids = _enabled_ids(app_info_id, agent_db)
    return [_row(get_or_create_baseline(agent_db, i)) for i in ids]


@router.get("/cost", response_model=schemas.CostOut)
def cost(
    app_info_id: Optional[int] = Query(None, gt=0),
    agent_db: Session = Depends(get_agent_db),
):
    ids = _enabled_ids(app_info_id, agent_db)
    today = date.today()
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

    agent_names = ["error_pattern", "token_bucket_health", "top_paths", "orchestrator"]

    by_agent = []
    for name in agent_names:
        if name == "orchestrator":
            q = agent_db.query(
                func.count(OrchestratorResult.id),
                func.sum(OrchestratorResult.tokens_used),
                func.sum(OrchestratorResult.cost_usd),
            ).filter(
                OrchestratorResult.app_info_id.in_(ids),
                OrchestratorResult.run_at >= today_start,
            )
        else:
            q = agent_db.query(
                func.count(AgentResult.id),
                func.sum(AgentResult.tokens_used),
                func.sum(AgentResult.cost_usd),
            ).filter(
                AgentResult.app_info_id.in_(ids),
                AgentResult.agent_name == name,
                AgentResult.run_at >= today_start,
            )
        cnt, tok, cst = q.first() or (0, 0, 0)
        by_agent.append(
            {
                "agent_name": name,
                "runs_today": int(cnt or 0),
                "tokens_today": int(tok or 0),
                "cost_today": round(float(cst or 0), 5),
            }
        )

    daily_series = []
    for i in range(6, -1, -1):
        day_start = today_start - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        entry: dict = {"date": (today - timedelta(days=i)).isoformat()}
        for name in agent_names:
            if name == "orchestrator":
                val = (
                    agent_db.query(func.sum(OrchestratorResult.cost_usd))
                    .filter(
                        OrchestratorResult.app_info_id.in_(ids),
                        OrchestratorResult.run_at >= day_start,
                        OrchestratorResult.run_at < day_end,
                    )
                    .scalar()
                )
            else:
                val = (
                    agent_db.query(func.sum(AgentResult.cost_usd))
                    .filter(
                        AgentResult.app_info_id.in_(ids),
                        AgentResult.agent_name == name,
                        AgentResult.run_at >= day_start,
                        AgentResult.run_at < day_end,
                    )
                    .scalar()
                )
            entry[name] = round(float(val or 0), 5)
        daily_series.append(entry)

    return {"by_agent": by_agent, "daily_series": daily_series}


@router.get("/outcomes", response_model=schemas.OutcomesSummaryOut)
def outcomes(
    app_info_id: Optional[int] = Query(None, gt=0),
    agent_db: Session = Depends(get_agent_db),
):
    ids = _enabled_ids(app_info_id, agent_db)

    measured_q = agent_db.query(ActionOutcome).filter(
        ActionOutcome.app_info_id.in_(ids),
        ActionOutcome.measured_at.isnot(None),
    )
    total_measured = measured_q.count()
    resolved_count = (
        measured_q.filter(ActionOutcome.anomaly_resolved.is_(True)).count()
    )
    rate = round(resolved_count / total_measured * 100, 1) if total_measured else 0.0

    recent = (
        agent_db.query(ActionOutcome)
        .filter(ActionOutcome.app_info_id.in_(ids))
        .order_by(ActionOutcome.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "total_measured": total_measured,
        "resolved_count": resolved_count,
        "resolution_rate_pct": rate,
        "recent": [_row(r) for r in recent],
    }


@router.get("/time-baseline", response_model=list[schemas.TimeBaselineItemOut])
def time_baseline_view(
    app_info_id: Optional[int] = Query(None, gt=0),
    agent_db: Session = Depends(get_agent_db),
):
    ids = _enabled_ids(app_info_id, agent_db)
    rows = (
        agent_db.query(TimeBaseline)
        .filter(TimeBaseline.app_info_id.in_(ids))
        .order_by(TimeBaseline.day_of_week, TimeBaseline.hour_of_day)
        .all()
    )
    return [_row(r) for r in rows]
