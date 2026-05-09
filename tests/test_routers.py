"""Router tests: input validation, happy-path responses, and response shape."""
import pytest


class TestHealthEndpoints:
    def test_liveness(self, test_client):
        r = test_client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestAgentsRouter:
    def test_history_empty_db(self, test_client):
        r = test_client.get("/agents/history")
        assert r.status_code == 200
        assert r.json() == []

    def test_history_limit_too_low(self, test_client):
        assert test_client.get("/agents/history?limit=0").status_code == 422

    def test_history_limit_too_high(self, test_client):
        assert test_client.get("/agents/history?limit=101").status_code == 422

    def test_history_negative_offset(self, test_client):
        assert test_client.get("/agents/history?offset=-1").status_code == 422

    def test_history_app_info_id_zero_rejected(self, test_client):
        assert test_client.get("/agents/history?app_info_id=0").status_code == 422

    def test_history_app_info_id_negative_rejected(self, test_client):
        assert test_client.get("/agents/history?app_info_id=-5").status_code == 422

    def test_history_valid_params_accepted(self, test_client):
        r = test_client.get("/agents/history?limit=10&offset=0&app_info_id=1")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestDashboardRouter:
    def test_apps_empty_db(self, test_client):
        r = test_client.get("/dashboard/apps")
        assert r.status_code == 200
        assert r.json() == []

    def test_timeline_invalid_filter_rejected(self, test_client):
        assert test_client.get("/dashboard/timeline?filter=garbage").status_code == 422

    def test_timeline_valid_filters_accepted(self, test_client):
        for f in ("all", "anomaly", "critical", "high", "medium", "low"):
            r = test_client.get(f"/dashboard/timeline?filter={f}")
            assert r.status_code == 200, f"filter={f!r} should be accepted"

    def test_timeline_invalid_agent_rejected(self, test_client):
        assert test_client.get("/dashboard/timeline?agent=invalid").status_code == 422

    def test_timeline_valid_agents_accepted(self, test_client):
        for a in ("all", "error", "token", "paths"):
            r = test_client.get(f"/dashboard/timeline?agent={a}")
            assert r.status_code == 200, f"agent={a!r} should be accepted"

    def test_timeline_limit_bounds(self, test_client):
        assert test_client.get("/dashboard/timeline?limit=0").status_code == 422
        assert test_client.get("/dashboard/timeline?limit=101").status_code == 422

    def test_summary_returns_expected_shape(self, test_client):
        r = test_client.get("/dashboard/summary")
        assert r.status_code == 200
        data = r.json()
        assert "total_agent_runs" in data
        assert "total_orchestrator_runs" in data
        assert "total_cost_usd" in data

    def test_baseline_empty_db(self, test_client):
        r = test_client.get("/dashboard/baseline")
        assert r.status_code == 200
        assert r.json() == []

    def test_cost_returns_expected_shape(self, test_client):
        r = test_client.get("/dashboard/cost")
        assert r.status_code == 200
        data = r.json()
        assert "by_agent" in data
        assert "daily_series" in data
        assert len(data["daily_series"]) == 7


class TestEvalsRouter:
    def test_results_limit_bounds(self, test_client):
        assert test_client.get("/evals/results?limit=0").status_code == 422
        assert test_client.get("/evals/results?limit=1001").status_code == 422

    def test_results_empty_db(self, test_client):
        r = test_client.get("/evals/results")
        assert r.status_code == 200
        assert r.json() == []

    def test_summary_empty_db(self, test_client):
        r = test_client.get("/evals/summary")
        assert r.status_code == 200
        assert r.json() == []
