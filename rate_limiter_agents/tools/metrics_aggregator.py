from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def build_error_summary(logs: list, window_minutes: int = 15, per_ip_address: bool = False) -> dict[str, Any]:
    total   = len(logs)
    blocked = sum(1 for l in logs if l.was_blocked)
    block_rate = (blocked / total * 100) if total > 0 else 0.0

    code_counts = Counter(l.response_code for l in logs if l.response_code)
    r_2xx = sum(v for k, v in code_counts.items() if 200 <= k < 300)
    r_4xx = sum(v for k, v in code_counts.items() if 400 <= k < 500)
    r_429 = code_counts.get(429, 0)
    r_5xx = sum(v for k, v in code_counts.items() if 500 <= k < 600)
    error_rate = ((r_4xx + r_5xx) / total * 100) if total > 0 else 0.0

    error_logs = [l for l in logs if l.response_code and l.response_code >= 400]
    top_error_paths = [
        {"path": p, "count": c}
        for p, c in Counter(l.request_path for l in error_logs if l.request_path).most_common(5)
    ]
    top_block_reasons = [
        {"reason": r, "count": c}
        for r, c in Counter(l.reason for l in logs if l.was_blocked and l.reason).most_common(5)
    ]

    result: dict[str, Any] = {
        "window_minutes": window_minutes,
        "total_requests": total,
        "response_2xx": r_2xx,
        "response_4xx": r_4xx,
        "response_429": r_429,
        "response_5xx": r_5xx,
        "blocked_requests": blocked,
        "block_rate_pct": round(block_rate, 2),
        "error_rate_pct": round(error_rate, 2),
        "top_error_paths": top_error_paths,
        "top_block_reasons": top_block_reasons,
    }

    if per_ip_address:
        ip_blocks = Counter(l.client_ip for l in logs if l.was_blocked and l.client_ip)
        unique_ips = len(set(l.client_ip for l in logs if l.client_ip))
        top_ip_count = ip_blocks.most_common(1)[0][1] if ip_blocks else 0
        result["unique_ips"] = unique_ips
        result["top_blocking_ips"] = [
            {"ip": ip, "blocks": c} for ip, c in ip_blocks.most_common(5)
        ]
        result["ip_concentration_pct"] = round(top_ip_count / blocked * 100, 2) if blocked else 0.0

    return result


def build_token_health_summary(logs: list, window_minutes: int = 15, per_ip_address: bool = False) -> dict[str, Any]:
    total  = len(logs)
    tokens = [l.remaining_tokens for l in logs if l.remaining_tokens is not None]

    if not tokens:
        return {
            "window_minutes": window_minutes,
            "total_requests": total,
            "no_token_data": True,
        }

    avg_tok = sum(tokens) / len(tokens)
    max_tok = max(tokens)
    min_tok = min(tokens)
    threshold = max_tok * 0.1 if max_tok > 0 else 10

    near_depletion     = sum(1 for t in tokens if 0 < t <= threshold)
    near_depletion_pct = (near_depletion / len(tokens) * 100) if tokens else 0.0
    depleted           = sum(1 for t in tokens if t == 0)

    path_tokens: dict[str, list[int]] = defaultdict(list)
    for l in logs:
        if l.request_path and l.remaining_tokens is not None:
            path_tokens[l.request_path].append(l.remaining_tokens)

    top_consuming = sorted(
        {p: sum(ts) / len(ts) for p, ts in path_tokens.items()}.items(),
        key=lambda x: x[1],
    )[:5]

    result: dict[str, Any] = {
        "window_minutes": window_minutes,
        "total_requests": total,
        "avg_remaining_tokens": round(avg_tok, 2),
        "min_remaining_tokens": min_tok,
        "max_remaining_tokens": max_tok,
        "depleted_count": depleted,
        "near_depletion_count": near_depletion,
        "near_depletion_pct": round(near_depletion_pct, 2),
        "top_token_consuming_paths": [
            {"path": p, "avg_remaining": round(v, 2)} for p, v in top_consuming
        ],
    }

    if per_ip_address:
        ip_tokens: dict[str, list[int]] = defaultdict(list)
        for l in logs:
            if l.client_ip and l.remaining_tokens is not None:
                ip_tokens[l.client_ip].append(l.remaining_tokens)

        ips_near_depletion = sum(
            1 for tks in ip_tokens.values() if any(0 < t <= threshold for t in tks)
        )
        ips_depleted = sum(
            1 for tks in ip_tokens.values() if any(t == 0 for t in tks)
        )
        result["unique_ips"] = len(ip_tokens)
        result["ips_near_depletion"] = ips_near_depletion
        result["ips_depleted"] = ips_depleted

    return result


def build_top_paths_summary(logs: list, window_minutes: int = 60, per_ip_address: bool = False) -> dict[str, Any]:
    total = len(logs)

    path_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "blocked": 0, "methods": Counter()})
    for l in logs:
        if l.request_path:
            path_stats[l.request_path]["total"] += 1
            if l.was_blocked:
                path_stats[l.request_path]["blocked"] += 1
            if l.http_method:
                path_stats[l.request_path]["methods"][l.http_method] += 1

    top_by_traffic = sorted(path_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:10]
    top_by_block   = sorted(
        [(p, s) for p, s in path_stats.items() if s["total"] >= 3],
        key=lambda x: x[1]["blocked"] / x[1]["total"],
        reverse=True,
    )[:5]

    def _fmt(p: str, s: dict) -> dict:
        br = round(s["blocked"] / s["total"] * 100, 2) if s["total"] else 0
        top_method = s["methods"].most_common(1)[0][0] if s["methods"] else "unknown"
        return {"path": p, "total": s["total"], "blocked": s["blocked"],
                "block_rate_pct": br, "top_method": top_method}

    result: dict[str, Any] = {
        "window_minutes": window_minutes,
        "total_requests": total,
        "unique_paths": len(path_stats),
        "top_paths_by_traffic": [_fmt(p, s) for p, s in top_by_traffic],
        "top_paths_by_block_rate": [_fmt(p, s) for p, s in top_by_block],
    }

    if per_ip_address:
        ip_path_blocks: dict[str, Counter] = defaultdict(Counter)
        for l in logs:
            if l.client_ip and l.request_path and l.was_blocked:
                ip_path_blocks[l.request_path][l.client_ip] += 1

        for entry in result["top_paths_by_block_rate"]:
            path_ip_counts = ip_path_blocks.get(entry["path"], Counter())
            entry["top_blocking_ips"] = [
                {"ip": ip, "blocks": c} for ip, c in path_ip_counts.most_common(3)
            ]

        result["unique_ips"] = len(set(l.client_ip for l in logs if l.client_ip))

    return result
