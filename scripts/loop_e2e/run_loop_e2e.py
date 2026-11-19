"""Loop Studio 五阶段闭环 E2E（父进程）：命令层 + 真 Worker 子进程 + 假 LLM。

覆盖：start_loop → 第 1 轮五阶段 → awaiting_decision → iterate → 第 2 轮 →
ship 收口；每一步都断言真实库里的 Run/Step/事件/产物/评分。

运行（仓库任意位置均可）：

    cd backend && ../.venv/Scripts/python.exe ../scripts/loop_e2e/run_loop_e2e.py

退出码 0 且末行 `LOOP_E2E_OK` 表示全部断言通过。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
PY = REPO / ".venv" / "Scripts" / "python.exe"
WORKER_SCRIPT = pathlib.Path(__file__).resolve().with_name("fake_worker.py")
TMP_ROOT = BACKEND / ".tmp_tests"

sys.path.insert(0, str(BACKEND))

import domain.contracts as C  # noqa: E402
from repositories import database, loop_repo, run_repo, session_repo  # noqa: E402
from repositories.database import session_scope  # noqa: E402
from services import command_service  # noqa: E402

OWNER = "loop-e2e@example.com"
NOW_ISO = C.utc_now_iso()
CLOCK = {"epoch": int(time.time())}
DB_URL = ""
RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail and not ok else ""))


def submit(session_id: str, action: C.Action, payload: dict, *, version: int | None = None):
    CLOCK["epoch"] += 70
    with session_scope() as db:
        command = C.CommandRequest(
            request_id=C.new_request_id(), action=action, expected_session_version=version, payload=payload
        )
        return command_service.submit_command(
            db, owner_email=OWNER, session_id=session_id, command=command,
            now_iso=NOW_ISO, now_epoch=CLOCK["epoch"],
        )


def run_worker(max_runs: int = 1) -> dict:
    env = dict(__import__("os").environ)
    env["DATABASE_URL"] = DB_URL
    env["MAX_RUNS"] = str(max_runs)
    env["WORKER"] = f"loop-e2e-{CLOCK['epoch']}"
    env["PYTHONPATH"] = str(BACKEND)
    proc = subprocess.run([str(PY), str(WORKER_SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=180, cwd=str(BACKEND))
    if proc.returncode != 0:
        raise RuntimeError(f"worker failed rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main() -> int:
    global DB_URL
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="loop_e2e_", dir=str(TMP_ROOT))
    DB_URL = "sqlite+pysqlite:///" + (pathlib.Path(tmp) / "loop.db").as_posix()
    database.configure(DB_URL, force=True)
    database.initialize_database(database.get_engine(), drop_first=True)

    with session_scope() as db:
        session = session_repo.create_session(
            db, session_id=C.new_id(), owner_email=OWNER,
            problem_statement="闭环 E2E：给 3-10 人财务团队做预算协同工具", now_iso=NOW_ISO,
        )
        sid = session.id
        version = session_repo.get_product_state(db, sid).version
    submit(sid, C.Action.revise_brief, {
        "expected_product_version": version,
        "fields": {
            "target_users": {"value": "3-10 人财务团队"},
            "usage_scenario": {"value": "同一预算表内多人协同编制"},
            "core_problem": {"value": "Excel 版本混乱、审批慢"},
            "success_criteria": {"value": "两人同时编辑 5 秒内同步"},
        },
    })
    submit(sid, C.Action.confirm_brief, {"expected_product_version": version + 1})

    with session_scope() as db:
        session = session_repo.require_session(db, sid, owner_email=OWNER)
        check("会话进入 brainstorming", session.phase == "brainstorming", session.phase)

    start = submit(sid, C.Action.start_loop, {
        "scope": "mvp", "max_rounds": 3, "target_score": 85, "focus": "多人协同与偏差提醒",
    })
    check("start_loop 返回 loop_id", bool(start.loop_id), str(start.loop_id))
    check("start_loop 建第 1 轮 Run", bool(start.run_id))

    out = run_worker(1)
    check("worker 领取并执行 1 个 Run", out.get("processed") == 1, json.dumps(out))

    with session_scope() as db:
        run = run_repo.get_run(db, start.run_id)
        check("第 1 轮 Run 成功终结", run.status == "succeeded", run.status)
        loop = loop_repo.get_loop(db, start.loop_id)
        check("闭环进入 awaiting_decision", loop.status == "awaiting_decision", loop.status)
        check("轮次为 1", loop.current_round == 1, str(loop.current_round))
        check("loop_version 推进", loop.loop_version >= 2, str(loop.loop_version))

        steps = run_repo.list_steps(db, start.run_id)
        stage_steps = [s for s in steps if s.logical_key.startswith("loop.stage.")]
        check("五个阶段 step 全部存在", len(stage_steps) == 5, str([s.logical_key for s in steps]))
        check("五个阶段 step 全部 succeeded",
              len(stage_steps) == 5 and all(s.status == "succeeded" for s in stage_steps),
              str([(s.logical_key, s.status) for s in stage_steps]))

        events = run_repo.events_after(db, run.id)
        types = [e.type for e in events]
        terminal = [t for t in types if t in C.TERMINAL_EVENT_TYPES]
        check("恰好一个终结事件", len(terminal) == 1, str(types))
        check("事件含 run.started", "run.started" in types, str(types))
        check("每阶段一条 loop.updated", types.count("loop.updated") == 5, str(types))
        check("一次 product_state.updated", types.count("product_state.updated") == 1, str(types))
        check("不是 FEATURE_NOT_READY", run.error_code in (None, ""), str(run.error_code))

        artifacts = loop_repo.list_artifacts(db, loop_id=start.loop_id)
        kinds = sorted(a.kind for a in artifacts)
        check("五类产物齐全", kinds == ["mvp_spec", "prototype", "scorecard", "test_report", "validation"], str(kinds))
        score_art = [a for a in artifacts if a.kind == "scorecard"]
        card = json.loads(score_art[0].payload_json) if score_art else {}
        check("评分卡 total 为 88", card.get("total") == 88, str(card.get("total")))
        check("verdict 为 pass", str(card.get("verdict")) == "pass", str(card.get("verdict")))

        state = session_repo.get_product_state(db, sid)
        check("产品状态版本推进", state.version >= 2, str(state.version))
        check("claims 收编 must-have 功能",
              any(getattr(c, "kind", "") == "feature" for c in state.claims),
              str([getattr(c, "kind", "") for c in state.claims]))

    iterate = submit(sid, C.Action.loop_decision, {
        "loop_id": start.loop_id, "expected_loop_version": loop.loop_version,
        "decision": "iterate", "note": "补验证邮件提醒通道",
    })
    check("iterate 建第 2 轮 Run", bool(iterate.run_id))
    run_worker(1)
    with session_scope() as db:
        run2 = run_repo.get_run(db, iterate.run_id)
        check("第 2 轮 Run 成功终结", run2.status == "succeeded", run2.status)
        loop = loop_repo.get_loop(db, start.loop_id)
        check("第 2 轮后回到 awaiting_decision", loop.status == "awaiting_decision", loop.status)
        check("current_round 为 2", loop.current_round == 2, str(loop.current_round))
        rounds = loop_repo.list_rounds(db, start.loop_id)
        check("两轮记录均 done", [r.status for r in rounds] == ["done", "done"], str([r.status for r in rounds]))

    ship = submit(sid, C.Action.loop_decision, {
        "loop_id": start.loop_id, "expected_loop_version": loop.loop_version,
        "decision": "ship", "note": "交付主流程",
    })
    check("ship 同步成功", ship.status == "succeeded", str(ship.status))
    with session_scope() as db:
        loop = loop_repo.get_loop(db, start.loop_id)
        check("闭环状态 shipped", loop.status == "shipped", loop.status)
        check("记录决策备注", loop.decision_note == "交付主流程", str(loop.decision_note))
        session = session_repo.require_session(db, sid, owner_email=OWNER)
        check("释放 active_run_id", session.active_run_id is None, str(session.active_run_id))

    database.dispose_engine()
    shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n===== {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS =====")
    if failed:
        for label, _, detail in failed:
            print("FAILED:", label, detail)
        return 1
    print("LOOP_E2E_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
