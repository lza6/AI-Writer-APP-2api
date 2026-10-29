"""Playwright 端到端验证 demo 页：真实请求 + 原生模式 + MOCK 模式。

用法:
  python verify_demo.py                 # 真实上游 (8951) + MOCK (8952)
  python verify_demo.py --real-only
"""
from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright


def run_case(page, base: str, mode: str, feature: str, expect_ok: bool) -> bool:
    page.goto(base, wait_until="domcontentloaded")
    page.wait_for_selector("#healthTag")
    page.wait_for_timeout(400)
    tag = page.inner_text("#healthTag")
    page.select_option("#mode", mode)
    if mode == "native":
        page.select_option("#feature", feature)
    page.fill("#input", "Write one short paragraph about why documentation matters.")
    page.select_option("#effort", "minimal")
    page.click("#run")
    page.wait_for_function(
        "() => { const t = document.querySelector('#textOut').textContent || ''; "
        "return t && t !== '等待请求…' && document.querySelector('#run').disabled === false; }",
        timeout=240_000,
    )
    text = page.inner_text("#textOut")
    meta = page.inner_text("#meta")
    ok = ("MOCK" in text) if not expect_ok else ("错误" not in text and len(text) > 10)
    print(f"  [{base} | {mode}/{feature}] health={tag!r}")
    print(f"    meta: {meta.replace(chr(10), ' ')[:150]}")
    print(f"    输出: {text[:120]!r}")
    print(f"    判定: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", default="http://127.0.0.1:8951")
    ap.add_argument("--mock", default="http://127.0.0.1:8952")
    ap.add_argument("--real-only", action="store_true")
    args = ap.parse_args()

    results: list[bool] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1360, "height": 900})
        try:
            # MOCK 场景：前端链路本身（不依赖上游额度）
            if not args.real_only:
                results.append(run_case(page, args.mock, "chat", "", expect_ok=False))
                page.screenshot(path="shot-mock-chat.png", full_page=True)
                results.append(run_case(page, args.mock, "native", "rephrase", expect_ok=False))
                page.screenshot(path="shot-mock-native.png", full_page=True)
            # 真实上游
            results.append(run_case(page, args.real, "chat", "", expect_ok=True))
            page.screenshot(path="shot-real-chat.png", full_page=True)
            results.append(run_case(page, args.real, "native", "writing", expect_ok=True))
            page.screenshot(path="shot-real-native.png", full_page=True)
        finally:
            browser.close()

    print(f"\n结果: {sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
