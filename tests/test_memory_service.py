from rate_limiter_agents.models import AgentResult, BaselineMemory
from rate_limiter_agents.tools.memory_service import (
    _EWMA_ALPHA,
    get_or_create_baseline,
    get_time_baseline,
    update_baseline,
    update_time_baseline,
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
    def test_updates_avg_rps_via_ewma(self, agent_db):
        # EWMA: new = alpha * sample + (1-alpha) * old
        get_or_create_baseline(agent_db, app_info_id=200)
        update_baseline(agent_db, 200, [AgentResult(app_info_id=200, peak_rps=200.0)])
        b = agent_db.query(BaselineMemory).filter_by(app_info_id=200).first()
        expected = _EWMA_ALPHA * 200.0 + (1 - _EWMA_ALPHA) * 100.0
        assert abs(float(b.avg_rps_7d) - expected) < 0.01
        assert b.sample_count == 1

    def test_spike_threshold_low_block_rate(self, agent_db):
        get_or_create_baseline(agent_db, app_info_id=201)
        update_baseline(
            agent_db, 201, [AgentResult(app_info_id=201, block_rate_pct=1.0)]
        )
        b = agent_db.query(BaselineMemory).filter_by(app_info_id=201).first()
        assert float(b.spike_threshold) == 2.5  # avg_block_rate_7d < 5.0

    def test_spike_threshold_high_block_rate(self, agent_db):
        # Need a block rate that pushes the EWMA above 20%.
        # Initial avg_block_rate_7d = 5.0; supply 250.0 so EWMA crosses 20.
        get_or_create_baseline(agent_db, app_info_id=202)
        for _ in range(40):
            update_baseline(
                agent_db, 202, [AgentResult(app_info_id=202, block_rate_pct=250.0)]
            )
        b = agent_db.query(BaselineMemory).filter_by(app_info_id=202).first()
        assert float(b.spike_threshold) == 4.0  # avg_block_rate_7d > 20.0

    def test_sample_count_caps_at_9999(self, agent_db):
        b = get_or_create_baseline(agent_db, app_info_id=203)
        b.sample_count = 9999
        agent_db.commit()
        update_baseline(agent_db, 203, [])
        agent_db.refresh(b)
        assert b.sample_count == 9999

    def test_empty_results_does_not_change_averages(self, agent_db):
        b = get_or_create_baseline(agent_db, app_info_id=204)
        initial_rps = float(b.avg_rps_7d)
        update_baseline(agent_db, 204, [])
        agent_db.refresh(b)
        assert float(b.avg_rps_7d) == initial_rps


class TestTimeBaseline:
    def test_creates_bucket_on_first_update(self, agent_db):
        result = AgentResult(app_info_id=300, block_rate_pct=10.0)
        update_time_baseline(agent_db, 300, result)
        bucket = get_time_baseline(agent_db, 300)
        assert bucket is not None
        assert bucket.sample_count == 1

    def test_ewma_converges_toward_new_value(self, agent_db):
        result = AgentResult(app_info_id=301, block_rate_pct=50.0)
        for _ in range(5):
            update_time_baseline(agent_db, 301, result)
        bucket = get_time_baseline(agent_db, 301)
        # After 5 iterations starting from 5.0, EWMA should be moving toward 50.0
        assert float(bucket.avg_block_rate_ewma) > 5.0
        assert float(bucket.avg_block_rate_ewma) < 50.0

    def test_no_update_when_block_rate_absent(self, agent_db):
        # First call seeds the bucket
        update_time_baseline(agent_db, 302, AgentResult(app_info_id=302, block_rate_pct=10.0))
        bucket = get_time_baseline(agent_db, 302)
        initial = float(bucket.avg_block_rate_ewma)
        # Second call with no block_rate_pct should not change the EWMA
        update_time_baseline(agent_db, 302, AgentResult(app_info_id=302))
        agent_db.refresh(bucket)
        assert float(bucket.avg_block_rate_ewma) == initial
