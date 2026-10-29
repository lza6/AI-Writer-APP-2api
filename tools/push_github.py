"""后台重试器：GitHub 二级限流时反复尝试建仓 + 推送 + 打 topics。

用法: python tools/push_github.py [--interval 300] [--attempts 12]
日志: push.log
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime

REPO = "lza6/AI-Writer-APP-2api"
DESC = ("AI Writer v1.0.10 (com.smartwidgetlabs.ai.writer) APK reverse engineering + pure-protocol 2API: "
        "client-side self-signed HS256 JWT from Firebase Remote Config defaults, upstream Azure OpenAI "
        "(gpt-5-mini/gpt-5-nano). OpenAI-compatible gateway + demo console + stress bench.")
TOPICS = ["apk-reverse", "2api", "reverse-engineering", "openai-api", "azure-openai", "jwt", "kotlin", "new-api"]


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open("push.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def create_repo() -> bool:
    code, out = run(["gh", "api", "-X", "POST", "user/repos", "-f", f"name={REPO.split('/')[1]}",
                     "-F", "private=false", "-f", f"description={DESC}"])
    if code == 0:
        log("repo created")
        return True
    if "already exists" in out or "Name already exists" in out:
        log("repo already exists")
        return True
    log(f"create failed: {out.strip()[:220]}")
    return False


def push() -> bool:
    code, out = run(["git", "remote", "add", "origin", f"https://github.com/{REPO}.git"])
    if code != 0:
        run(["git", "remote", "set-url", "origin", f"https://github.com/{REPO}.git"])
    code, out = run(["git", "push", "-u", "origin", "main"])
    if code == 0:
        log("pushed")
        return True
    log(f"push failed: {out.strip()[:400]}")
    return False


def set_topics() -> None:
    payload = json.dumps({"names": TOPICS})
    code, out = run(["gh", "api", "-X", "PUT", f"repos/{REPO}/topics", "--input", "-"])
    if code != 0:
        # gh 不接受 stdin 输入时退化为逐字段形式
        args = ["gh", "api", "-X", "PUT", f"repos/{REPO}/topics"]
        for t in TOPICS:
            args += ["-f", f"names[]={t}"]
        code, out = run(args)
    log("topics set" if code == 0 else f"topics failed: {out.strip()[:160]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--attempts", type=int, default=12)
    args = ap.parse_args()

    for i in range(1, args.attempts + 1):
        log(f"attempt {i}/{args.attempts}")
        if create_repo() and push():
            set_topics()
            log(f"DONE https://github.com/{REPO}")
            sys.exit(0)
        if i < args.attempts:
            log(f"sleep {args.interval}s")
            time.sleep(args.interval)
    log("FAILED all attempts")
    sys.exit(1)


if __name__ == "__main__":
    main()
