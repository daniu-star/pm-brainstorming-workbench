"""线上浏览器上下文网络采样：在同一页面里连发 fetch，统计失败率与耗时分布。

用途：区分「浏览器同源请求 + HTTP/2」与 curl 的差异，验证 `网络连接失败` 是否可复现。
用法：.venv\\Scripts\\python.exe scripts\\sample_live_network.py --rounds 30
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "browser_e2e"))

import cdp  # noqa: E402

WEB = "https://www.brainstorming.top"
PROBE_JS = """
(async (paths) => {
  const out = [];
  for (let i = 0; i < paths.length; i += 1) {
    const path = paths[i];
    const started = performance.now();
    try {
      const res = await fetch(path, { credentials: "include", cache: "no-store" });
      out.push({ path, ok: true, status: res.status, ms: Math.round(performance.now() - started) });
    } catch (err) {
      out.push({ path, ok: false, error: String(err && err.message ? err.message : err), ms: Math.round(performance.now() - started) });
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return out;
})(%s)
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=20, help="每条路径采样次数")
    args = parser.parse_args(argv)

    paths: list[str] = []
    for _ in range(args.rounds):
        paths.extend(["/backend/health", "/backend/api/v2/llm-config"])

    browser = cdp.Browser(window_size="1280,900")
    try:
        page = browser.new_page()
        page.goto(WEB + "/", timeout=60, expect="document.body.innerText.length > 0")
        raw = page.eval(PROBE_JS % json.dumps(paths), timeout=600)
        rows = json.loads(raw) if isinstance(raw, str) else raw
    finally:
        browser.close()

    by_path: dict[str, dict[str, float]] = {}
    failures = 0
    for row in rows:
        stat = by_path.setdefault(row["path"], {"n": 0, "fail": 0, "ms": []})
        stat["n"] += 1
        if not row["ok"]:
            stat["fail"] += 1
            failures += 1
            print("FAIL", row)
        else:
            stat["ms"].append(row["ms"])

    print("TOTAL", len(rows), "FAILURES", failures)
    for path, stat in by_path.items():
        times = sorted(stat["ms"])
        if times:
            median = times[len(times) // 2]
            print(f"{path}: n={stat['n']} fail={stat['fail']} median={median}ms max={times[-1]}ms")
        else:
            print(f"{path}: n={stat['n']} fail={stat['fail']}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
