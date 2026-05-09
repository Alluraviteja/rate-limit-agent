"""Tests for the _parse / _num helpers shared across all agent modules."""

from rate_limiter_agents.agents.error_pattern import _num, _parse


class TestParse:
    def test_parses_full_response(self):
        text = (
            "ANOMALY: YES\n"
            "SEVERITY: high\n"
            "BLOCK_RATE: 35%\n"
            "REASON: High block rate detected\n"
            "ACTION: throttle"
        )
        r = _parse(text)
        assert r["ANOMALY"] == "YES"
        assert r["SEVERITY"] == "high"
        assert r["BLOCK_RATE"] == "35"
        assert r["REASON"] == "High block rate detected"
        assert r["ACTION"] == "throttle"

    def test_strips_trailing_percent(self):
        r = _parse("BLOCK_RATE: 42%")
        assert r["BLOCK_RATE"] == "42"

    def test_empty_string_returns_empty_dict(self):
        assert _parse("") == {}

    def test_ignores_lines_without_colon(self):
        r = _parse("no colon here\nKEY: value")
        assert "no colon here" not in r
        assert r["KEY"] == "value"

    def test_reason_with_embedded_colon(self):
        # partition() splits on first colon only, keeping the rest of the value.
        # rstrip("%") then strips a trailing percent if present.
        r = _parse("REASON: Error rate spiked: 55%")
        assert r["REASON"] == "Error rate spiked: 55"

    def test_whitespace_trimmed(self):
        r = _parse("  KEY  :  value  ")
        assert r["KEY"] == "value"


class TestNum:
    def test_plain_float(self):
        assert _num("35.5") == 35.5

    def test_strips_percent(self):
        assert _num("42%") == 42.0

    def test_integer_string(self):
        assert _num("10") == 10.0

    def test_zero(self):
        assert _num("0") == 0.0

    def test_none_input(self):
        assert _num(None) is None

    def test_non_numeric_string(self):
        assert _num("not_a_number") is None

    def test_whitespace_around_value(self):
        assert _num(" 7.5 ") == 7.5
