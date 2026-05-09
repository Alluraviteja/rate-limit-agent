from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_agent_db
from ..evals.runner import run_evals
from ..models import EvalRunResult
from .. import schemas

router = APIRouter()


@router.post("/run", response_model=schemas.EvalRunTriggerOut)
def trigger_eval_run(agent_db: Session = Depends(get_agent_db)):
    """Run all eval scenarios and store results in eval_run_results table."""
    return run_evals(agent_db)


@router.get("/results", response_model=list[schemas.EvalRunOut])
def get_eval_results(
    limit: int = Query(200, ge=1, le=1000),
    agent_db: Session = Depends(get_agent_db),
):
    """Return recent eval results grouped by run, with per-run accuracy metrics."""
    rows = (
        agent_db.query(EvalRunResult)
        .order_by(EvalRunResult.run_at.desc())
        .limit(limit)
        .all()
    )

    runs: dict[str, dict] = {}
    for r in rows:
        run_id = str(r.run_id)
        if run_id not in runs:
            runs[run_id] = {
                "run_id": run_id,
                "run_at": r.run_at.isoformat() if r.run_at else None,
                "checks": [],
            }
        runs[run_id]["checks"].append({
            "scenario": r.scenario_name,
            "agent": r.agent_name,
            "expected_severity": r.expected_severity,
            "actual_severity": r.actual_severity,
            "expected_action": r.expected_action,
            "actual_action": r.actual_action,
            "severity_correct": r.severity_correct,
            "action_correct": r.action_correct,
            "tokens_used": r.tokens_used,
            "cost_usd": float(r.cost_usd or 0),
        })

    result = []
    for run in runs.values():
        checks = run["checks"]
        total = len(checks)
        sev_ok = sum(1 for c in checks if c["severity_correct"])
        act_ok = sum(1 for c in checks if c["action_correct"])
        total_cost = sum(c["cost_usd"] for c in checks)
        total_tokens = sum(c["tokens_used"] or 0 for c in checks)

        by_scenario: dict[str, dict] = defaultdict(lambda: {"total": 0, "sev_ok": 0, "act_ok": 0})
        for c in checks:
            s = by_scenario[c["scenario"]]
            s["total"] += 1
            s["sev_ok"] += int(c["severity_correct"])
            s["act_ok"] += int(c["action_correct"])

        result.append({
            "run_id": run["run_id"],
            "run_at": run["run_at"],
            "total_checks": total,
            "severity_accuracy_pct": round(sev_ok / total * 100, 1) if total else 0.0,
            "action_accuracy_pct": round(act_ok / total * 100, 1) if total else 0.0,
            "total_tokens_used": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "by_scenario": {
                name: {
                    "severity_accuracy_pct": round(v["sev_ok"] / v["total"] * 100, 1),
                    "action_accuracy_pct": round(v["act_ok"] / v["total"] * 100, 1),
                }
                for name, v in by_scenario.items()
            },
            "checks": checks,
        })

    return result


@router.get("/summary", response_model=list[schemas.EvalTrendItemOut])
def get_eval_summary(agent_db: Session = Depends(get_agent_db)):
    """Accuracy trend across all eval runs — one row per run."""
    rows = (
        agent_db.query(EvalRunResult)
        .order_by(EvalRunResult.run_at.asc())
        .all()
    )

    by_run: dict[str, list[EvalRunResult]] = defaultdict(list)
    for r in rows:
        by_run[str(r.run_id)].append(r)

    trend = []
    for run_id, results in by_run.items():
        total = len(results)
        sev_ok = sum(1 for r in results if r.severity_correct)
        act_ok = sum(1 for r in results if r.action_correct)
        cost = sum(float(r.cost_usd or 0) for r in results)
        trend.append({
            "run_id": run_id,
            "run_at": results[0].run_at.isoformat() if results[0].run_at else None,
            "total_checks": total,
            "severity_accuracy_pct": round(sev_ok / total * 100, 1) if total else 0.0,
            "action_accuracy_pct": round(act_ok / total * 100, 1) if total else 0.0,
            "total_cost_usd": round(cost, 6),
        })

    return trend
