"""AI Writer 2API 压测：并发阶梯 + 延迟分位 + 错误分布。

用法:
  python bench.py                       # 默认 2/4/8 并发，各 8 请求，真实上游
  python bench.py --mock 1              # 只打本地 MOCK（验证网关自身吞吐，不烧上游额度）
  python bench.py --concurrency 8 --requests 16 --levels ""   # 单点压测
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

PROMPTS = [
    "Write one short sentence about autumn.",
    "Rephrase: The meeting was postponed.",
    "Give me a subject line for a follow-up email.",
    "Summarise in one line: cats sleep a lot.",
    "Write a two-word slogan for a coffee shop.",
    "Name one benefit of daily reading.",
    "Rewrite politely: send me the file.",
    "One sentence about the ocean at night.",
]


def one_call(base: str, idx: int, mock: bool, timeout: int) -> dict:
    body = {
        "model": "gpt-5-mini" if not mock else "gpt-5-mini",
        "messages": [{"role": "user", "content": PROMPTS[idx % len(PROMPTS)]}],
        "reasoning_effort": "minimal",
        "max_completion_tokens": 120,
    }
    t0 = time.time()
    try:
        r = requests.post(base + "/v1/chat/completions", json=body, timeout=timeout)
        dt = time.time() - t0
        try:
            d = r.json()
        except ValueError:
            return {"ok": False, "dt": dt, "status": r.status_code, "err": "non-json", "tokens": 0}
        if r.status_code == 200 and d.get("choices"):
            return {"ok": True, "dt": dt, "status": 200, "err": "", "tokens": (d.get("usage") or {}).get("total_tokens", 0)}
        err = (d.get("error") or {}).get("message", "") if isinstance(d.get("error"), dict) else str(d.get("error"))
        return {"ok": False, "dt": dt, "status": r.status_code, "err": (err or "")[:90], "tokens": 0}
    except requests.RequestException as exc:
        return {"ok": False, "dt": time.time() - t0, "status": 0, "err": type(exc).__name__, "tokens": 0}


def run_level(base: str, conc: int, total: int, mock: bool, timeout: int) -> dict:
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=conc) as ex:
        results = list(ex.map(lambda i: one_call(base, i, mock, timeout), range(total)))
    wall = time.time() - t0
    ok = [r for r in results if r["ok"]]
    lats = sorted(r["dt"] for r in ok)
    errs = Counter(r["err"] or f"HTTP {r['status']}" for r in results if not r["ok"])

    def pct(p: float) -> float:
        if not lats:
            return 0.0
        k = min(len(lats) - 1, int(round(p / 100 * (len(lats) - 1))))
        return round(lats[k], 2)

    return {
        "concurrency": conc,
        "requests": total,
        "success": len(ok),
        "success_rate": round(len(ok) / total * 100, 1),
        "rps": round(total / wall, 2),
        "wall_s": round(wall, 1),
        "p50": pct(50),
        "p90": pct(90),
        "max": round(lats[-1], 2) if lats else 0.0,
        "total_tokens": sum(r["tokens"] for r in ok),
        "errors": dict(errs),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8951")
    ap.add_argument("--levels", default="2,4,8", help="并发阶梯，逗号分隔；空串表示只用 --concurrency")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--requests", type=int, default=8)
    ap.add_argument("--mock", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    levels = [int(x) for x in args.levels.split(",") if x.strip()] or [args.concurrency]
    print(f"base={args.base} mock={bool(args.mock)} requests/level={args.requests} levels={levels}")
    rows = []
    for conc in levels:
        row = run_level(args.base, conc, args.requests, bool(args.mock), args.timeout)
        rows.append(row)
        print(f"  并发 {conc:3d} | 成功 {row['success']}/{row['requests']} ({row['success_rate']}%) "
              f"| {row['rps']} rps | p50 {row['p50']}s p90 {row['p90']}s max {row['max']}s "
              f"| tokens {row['total_tokens']} | 错误 {row['errors'] or '-'}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
        print("结果已写入", args.json)
    if rows:
        print("平均 p50:", round(statistics.mean(r["p50"] for r in rows), 2), "s")


if __name__ == "__main__":
    main()
