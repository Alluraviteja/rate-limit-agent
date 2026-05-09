from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import AgentResult, BaselineMemory, OrchestratorResult


def get_recent_context(db: Session, app_info_id: int, limit: int = 3) -> list[dict]:
    rows = (
        db.query(OrchestratorResult)
        .filter_by(app_info_id=app_info_id)
        .order_by(OrchestratorResult.run_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "run_at": r.run_at.isoformat() if r.run_at else None,
            "severity": r.final_severity,
            "action": r.action,
            "reason": r.reason,
        }
        for r in reversed(rows)  # chronological order
    ]


def get_or_create_baseline(db: Session, app_info_id: int) -> BaselineMemory:
    baseline = db.query(BaselineMemory).filter_by(app_info_id=app_info_id).first()
    if baseline is None:
        baseline = BaselineMemory(
            app_info_id=app_info_id,
            avg_rps_7d=100.0,
            avg_block_rate_7d=5.0,
            avg_bot_ratio_7d=2.0,
            spike_threshold=3.0,
            sample_count=0,
        )
        db.add(baseline)
        db.commit()
        db.refresh(baseline)
    return baseline


def update_baseline(
    db: Session, app_info_id: int, agent_results: list[AgentResult]
) -> None:
    baseline = get_or_create_baseline(db, app_info_id)
    n = int(baseline.sample_count or 0)

    peak_rps_vals = [float(r.peak_rps) for r in agent_results if r.peak_rps is not None]
    block_rate_vals = [float(r.block_rate_pct) for r in agent_results if r.block_rate_pct is not None]
    bot_ratio_vals = [float(r.bot_ratio_pct) for r in agent_results if r.bot_ratio_pct is not None]

    if peak_rps_vals:
        new_val = sum(peak_rps_vals) / len(peak_rps_vals)
        baseline.avg_rps_7d = (float(baseline.avg_rps_7d) * n + new_val) / (n + 1)

    if block_rate_vals:
        new_val = sum(block_rate_vals) / len(block_rate_vals)
        baseline.avg_block_rate_7d = (float(baseline.avg_block_rate_7d) * n + new_val) / (n + 1)

    if bot_ratio_vals:
        new_val = sum(bot_ratio_vals) / len(bot_ratio_vals)
        baseline.avg_bot_ratio_7d = (float(baseline.avg_bot_ratio_7d) * n + new_val) / (n + 1)

    avg_block = float(baseline.avg_block_rate_7d)
    if avg_block < 5.0:
        baseline.spike_threshold = 2.5
    elif avg_block > 20.0:
        baseline.spike_threshold = 4.0
    else:
        baseline.spike_threshold = 3.0

    baseline.sample_count = min(n + 1, 672)
    baseline.last_updated = datetime.now(timezone.utc)
    db.commit()
