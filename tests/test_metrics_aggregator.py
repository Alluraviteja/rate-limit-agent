from types import SimpleNamespace

from rate_limiter_agents.tools.metrics_aggregator import (
    build_error_summary,
    build_token_health_summary,
    build_top_paths_summary,
)


def _log(**kw):
    defaults = {
        "was_blocked": False,
        "response_code": 200,
        "request_path": "/api/v1/test",
        "http_method": "GET",
        "client_ip": "10.0.0.1",
        "remaining_tokens": 50,
        "reason": None,
    }
    return SimpleNamespace(**{**defaults, **kw})


class TestBuildErrorSummary:
    def test_empty_logs(self):
        r = build_error_summary([])
        assert r["total_requests"] == 0
        assert r["blocked_requests"] == 0
        assert r["block_rate_pct"] == 0.0

    def test_normal_traffic_no_blocks(self):
        logs = [_log() for _ in range(10)]
        r = build_error_summary(logs)
        assert r["total_requests"] == 10
        assert r["blocked_requests"] == 0
        assert r["error_rate_pct"] == 0.0

    def test_block_rate_calculation(self):
        logs = [_log(was_blocked=True, response_code=429) for _ in range(4)]
        logs += [_log() for _ in range(6)]
        r = build_error_summary(logs)
        assert r["blocked_requests"] == 4
        assert r["block_rate_pct"] == 40.0

    def test_5xx_error_rate(self):
        logs = [_log(response_code=500) for _ in range(2)]
        logs += [_log() for _ in range(8)]
        r = build_error_summary(logs)
        assert r["response_5xx"] == 2
        assert r["error_rate_pct"] == 20.0

    def test_top_error_paths(self):
        logs = [_log(response_code=404, request_path="/missing") for _ in range(3)]
        logs += [_log(response_code=500, request_path="/error") for _ in range(2)]
        r = build_error_summary(logs)
        paths = [e["path"] for e in r["top_error_paths"]]
        assert "/missing" in paths

    def test_per_ip_mode_adds_concentration(self):
        logs = [
            _log(was_blocked=True, client_ip="1.1.1.1"),
            _log(was_blocked=True, client_ip="1.1.1.1"),
            _log(client_ip="2.2.2.2"),
        ]
        r = build_error_summary(logs, per_ip_address=True)
        assert "ip_concentration_pct" in r
        assert r["unique_ips"] == 2
        assert r["ip_concentration_pct"] == 100.0  # both blocks from same IP


class TestBuildTokenHealthSummary:
    def test_empty_logs(self):
        r = build_token_health_summary([])
        assert r["total_requests"] == 0

    def test_no_token_data(self):
        logs = [_log(remaining_tokens=None) for _ in range(5)]
        r = build_token_health_summary(logs)
        assert r.get("no_token_data") is True

    def test_near_depletion_detected(self):
        # max=100 → threshold=10; tokens ≤10 but >0 count as near-depletion
        logs = [_log(remaining_tokens=100) for _ in range(5)]
        logs += [_log(remaining_tokens=5) for _ in range(5)]
        r = build_token_health_summary(logs)
        assert r["near_depletion_count"] == 5
        assert r["near_depletion_pct"] == 50.0

    def test_depleted_count(self):
        logs = [_log(remaining_tokens=0) for _ in range(3)]
        logs += [_log(remaining_tokens=100) for _ in range(7)]
        r = build_token_health_summary(logs)
        assert r["depleted_count"] == 3

    def test_per_ip_mode(self):
        logs = [
            _log(remaining_tokens=0, client_ip="1.1.1.1"),
            _log(remaining_tokens=50, client_ip="2.2.2.2"),
        ]
        r = build_token_health_summary(logs, per_ip_address=True)
        assert "unique_ips" in r
        assert r["unique_ips"] == 2
        assert r["ips_depleted"] == 1


class TestBuildTopPathsSummary:
    def test_empty_logs(self):
        r = build_top_paths_summary([])
        assert r["total_requests"] == 0
        assert r["unique_paths"] == 0

    def test_top_path_by_traffic(self):
        logs = [_log(request_path="/api/a") for _ in range(7)]
        logs += [_log(request_path="/api/b") for _ in range(3)]
        r = build_top_paths_summary(logs)
        assert r["unique_paths"] == 2
        assert r["top_paths_by_traffic"][0]["path"] == "/api/a"
        assert r["top_paths_by_traffic"][0]["total"] == 7

    def test_top_path_by_block_rate(self):
        # /api/attack: 4/4 blocked (100%) — qualifies since total >= 3
        logs = [_log(request_path="/api/attack", was_blocked=True) for _ in range(4)]
        logs += [_log(request_path="/api/normal") for _ in range(10)]
        r = build_top_paths_summary(logs)
        top = r["top_paths_by_block_rate"][0]
        assert top["path"] == "/api/attack"
        assert top["block_rate_pct"] == 100.0

    def test_paths_with_fewer_than_3_requests_excluded_from_block_rate(self):
        # only 2 requests on this path — should not appear in top_by_block_rate
        logs = [_log(request_path="/rare", was_blocked=True) for _ in range(2)]
        logs += [_log(request_path="/normal") for _ in range(10)]
        r = build_top_paths_summary(logs)
        block_paths = [e["path"] for e in r["top_paths_by_block_rate"]]
        assert "/rare" not in block_paths
