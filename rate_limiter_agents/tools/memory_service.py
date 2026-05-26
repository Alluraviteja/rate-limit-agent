from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import AgentResult, BaselineMemory, OrchestratorResult, TimeBaseline

# Alpha=0.1 means each new sample contributes 10% weight.
# Responds to genuine trend shifts within ~10 samples without being
# thrown off by single-run outliers. Never freezes like the old
# incremental-mean formula did at sample_count=672.
_EWMA_ALPHA = 0.1


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

    peak_rps_vals = [float(r.peak_rps) for r in agent_results if r.peak_rps is not None]
    block_rate_vals = [
        float(r.block_rate_pct) for r in agent_results if r.block_rate_pct is not None
    ]
    bot_ratio_vals = [
        float(r.bot_ratio_pct) for r in agent_results if r.bot_ratio_pct is not None
    ]

    if peak_rps_vals:
        new_val = sum(peak_rps_vals) / len(peak_rps_vals)
        baseline.avg_rps_7d = (  # type: ignore[assignment]
            _EWMA_ALPHA * new_val + (1 - _EWMA_ALPHA) * float(baseline.avg_rps_7d)
        )

    if block_rate_vals:
        new_val = sum(block_rate_vals) / len(block_rate_vals)
        baseline.avg_block_rate_7d = (  # type: ignore[assignment]
            _EWMA_ALPHA * new_val + (1 - _EWMA_ALPHA) * float(baseline.avg_block_rate_7d)
        )

    if bot_ratio_vals:
        new_val = sum(bot_ratio_vals) / len(bot_ratio_vals)
        baseline.avg_bot_ratio_7d = (  # type: ignore[assignment]
            _EWMA_ALPHA * new_val + (1 - _EWMA_ALPHA) * float(baseline.avg_bot_ratio_7d)
        )

    avg_block = float(baseline.avg_block_rate_7d)
    if avg_block < 5.0:
        baseline.spike_threshold = 2.5  # type: ignore[assignment]
    elif avg_block > 20.0:
        baseline.spike_threshold = 4.0  # type: ignore[assignment]
    else:
        baseline.spike_threshold = 3.0  # type: ignore[assignment]

    # sample_count tracks how many observations this app has seen; no longer
    # used in the EWMA formula, but kept for observability (warm vs cold baseline).
    baseline.sample_count = min(int(baseline.sample_count or 0) + 1, 9999)  # type: ignore[assignment]
    baseline.last_updated = datetime.now(timezone.utc)  # type: ignore[assignment]
    db.commit()


# ── Time-bucketed baselines ──────────────────────────────────────────────────


def get_time_baseline(db: Session, app_info_id: int) -> TimeBaseline | None:
    """Return the bucket matching the current UTC hour and weekday, or None."""
    now = datetime.now(timezone.utc)
    return (
        db.query(TimeBaseline)
        .filter_by(
            app_info_id=app_info_id,
            hour_of_day=now.hour,
            day_of_week=now.weekday(),
        )
        .first()
    )


def update_time_baseline(
    db: Session, app_info_id: int, error_result: AgentResult
) -> None:
    """Update the EWMA block-rate baseline for the current hour/day-of-week bucket.

    Uses the error-pattern agent result because it carries the most reliable
    block_rate_pct for the observation window.
    """
    now = datetime.now(timezone.utc)
    hour = now.hour
    dow = now.weekday()

    bucket = (
        db.query(TimeBaseline)
        .filter_by(app_info_id=app_info_id, hour_of_day=hour, day_of_week=dow)
        .first()
    )
    if bucket is None:
        bucket = TimeBaseline(
            app_info_id=app_info_id,
            hour_of_day=hour,
            day_of_week=dow,
            avg_block_rate_ewma=5.0,
            avg_rps_ewma=100.0,
            sample_count=0,
        )
        db.add(bucket)

    if error_result.block_rate_pct is not None:
        current = float(error_result.block_rate_pct)
        bucket.avg_block_rate_ewma = (  # type: ignore[assignment]
            _EWMA_ALPHA * current + (1 - _EWMA_ALPHA) * float(bucket.avg_block_rate_ewma)
        )

    bucket.sample_count = min(int(bucket.sample_count or 0) + 1, 9999)  # type: ignore[assignment]
    bucket.last_updated = now  # type: ignore[assignment]
    db.commit()
