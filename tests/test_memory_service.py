from rate_limiter_agents.models import AgentResult, BaselineMemory
from rate_limiter_agents.tools.memory_service import (
    get_or_create_baseline,
    update_baseline,
)


class TestGetOrCreateBaseline:
    def test_creates_default_when_missing(self, agent_db):
        b = get_or_create_baseline(agent_db, app_info_id=100)
        assert b.app_info_id == 100
        assert b.sample_count == 0
        assert float(b.avg_rps_7d) == 100.0
        assert float(b.avg_block_rate_7d) == 5.0

    def test_returns_existing_without_duplicate(self, agent_db):
        b1 = get_or_create_baseline(agent_db, app_info_id=101)
        b2 = get_or_create_baseline(agent_db, app_info_id=101)
        assert b1.id == b2.id
        count = agent_db.query(BaselineMemory).filter_by(app_info_id=101).count()
        assert count == 1

    def test_different_ids_are_independent(self, agent_db):
        b1 = get_or_create_baseline(agent_db, app_info_id=102)
        b2 = get_or_create_baseline(agent_db, app_info_id=103)
        assert b1.id != b2.id


class TestUpdateBaseline:
    def test_updates_avg_rps(self, agent_db):
        get_or_create_baseline(agent_db, app_info_id=200)
        update_baseline(agent_db, 200, [AgentResult(app_info_id=200, peak_rps=200.0)])
        b = agent_db.query(BaselineMemory).filter_by(app_info_id=200).first()
        # Formula: (initial * n + new) / (n+1) = (100 * 0 + 200) / 1 = 200.0
        assert float(b.avg_rps_7d) == 200.0
        assert b.sample_count == 1

    def test_spike_threshold_low_block_rate(self, agent_db):
        get_or_create_baseline(agent_db, app_info_id=201)
        update_baseline(
            agent_db, 201, [AgentResult(app_info_id=201, block_rate_pct=1.0)]
        )
        b = agent_db.query(BaselineMemory).filter_by(app_info_id=201).first()
        assert float(b.spike_threshold) == 2.5  # avg_block_rate_7d < 5.0

    def test_spike_threshold_high_block_rate(self, agent_db):
        get_or_create_baseline(agent_db, app_info_id=202)
        update_baseline(
            agent_db, 202, [AgentResult(app_info_id=202, block_rate_pct=25.0)]
        )
        b = agent_db.query(BaselineMemory).filter_by(app_info_id=202).first()
        assert float(b.spike_threshold) == 4.0  # avg_block_rate_7d > 20.0

    def test_sample_count_caps_at_672(self, agent_db):
        b = get_or_create_baseline(agent_db, app_info_id=203)
        b.sample_count = 672
        agent_db.commit()
        update_baseline(agent_db, 203, [])
        agent_db.refresh(b)
        assert b.sample_count == 672

    def test_empty_results_does_not_change_averages(self, agent_db):
        b = get_or_create_baseline(agent_db, app_info_id=204)
        initial_rps = float(b.avg_rps_7d)
        update_baseline(agent_db, 204, [])
        agent_db.refresh(b)
        assert float(b.avg_rps_7d) == initial_rps
