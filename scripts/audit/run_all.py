"""一键审计入口：运行全部检查并生成 docs/audit/report.md，同时回写 checklist.md 状态。"""
from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[1]

from common import _results  # noqa: E402


def run_smtp_checks() -> None:
    print("\n== SMTP 工具链路 ==")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "test_smtp.py"),
                           "--check-config"], capture_output=True, text=True)
    out = proc.stdout or ""
    ok = proc.returncode == 0 and "PASS" in out
    from common import check
    check("C001-C004", ok, f"SMTP 配置/AUTH 密钥/health 模拟（exit={proc.returncode}）")


def main() -> int:
    run_smtp_checks()
    import check_security  # noqa: F401
    check_security.run()
    import check_backend_logic
    check_backend_logic.run()
    import check_frontend
    check_frontend.run()
    import check_interview
    check_interview.run()
    import check_engineering
    check_engineering.run()
    import check_round1
    check_round1.run()
    import check_round2
    check_round2.run()

    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = [(cid, d) for cid, ok, d in _results if not ok]

    lines = [
        "# PM Brainstorm 审计报告",
        "",
        f"生成时间：{datetime.datetime.now().isoformat(timespec='seconds')}",
        f"结果：**{passed}/{total} 通过**",
        "",
        "## 明细",
        "",
        "| 检查项 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    for cid, ok, detail in _results:
        lines.append(f"| {cid} | {'PASS' if ok else '**FAIL**'} | {detail} |")
    if failed:
        lines += ["", "## 未通过项", ""]
        lines += [f"- {cid}: {d}" for cid, d in failed]
    report = ROOT / "docs" / "audit" / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")

    # 回写 checklist.md 状态
    cl = ROOT / "docs" / "audit" / "checklist.md"
    text = cl.read_text(encoding="utf-8")
    for cid, ok, detail in _results:
        cids = cid.split("/")[0].split("b")[0]  # C011b -> C011
        if re.match(r"^C\d+$", cid) or re.match(r"^C\d+[-/]C\d+$", cid) or cid.endswith("b"):
            key = cid.rstrip("b") if cid.endswith("b") else cid
            pattern = re.compile(r"(- \[)\s\]( \*\*" + re.escape(key) + r"\*\*)")
            text = pattern.sub(lambda m: m.group(1) + ("x" if ok else "!") + "]" + m.group(2), text)
    cl.write_text(text, encoding="utf-8")

    print(f"\n==== 总计 {passed}/{total} 通过 ====")
    if failed:
        print("未通过：")
        for cid, d in failed:
            print(f"  - {cid}: {d}")
    report_rel = report.relative_to(ROOT)
    print(f"报告已写入 {report_rel}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
