"""跨进程验收（PRD §11.1 / AC-06 / AC-07 / AC-08 / AC-28）。

本模块**真的启动独立 OS 进程**（``subprocess`` + 独立解释器），它们通过同一个
``DATABASE_URL`` 共享 SQLite 数据库，用来验证：

* AC-06：两个 API 进程用同一个 ``request_id`` 提交 → 只产生一个 Run / 一条用户消息；
* AC-07：两个进程用不同键并发改同一会话 → 只有一个占位成功，另一个 SESSION_BUSY；
* AC-08：两个 Worker 进程同时领取同一 Run → 只领取一次，事件不重复；
* AC-28：验证码/限流状态跨进程共享（进程 A 发码，进程 B 校验）。

这些用例不能退化成线程/同进程连接：每个子进程都是新的解释器与独立 SQLite 连接。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:  # pragma: no cover - 直接运行单文件时兜底
    sys.path.insert(0, str(BACKEND))

from domain import contracts as C  # noqa: E402
from repositories import auth_repo, database, run_repo, session_repo  # noqa: E402

OWNER = "cross-process@example.com"

# 子进程公共前导：只用环境变量定位数据库，不做任何父进程状态继承。
_CHILD_PRELUDE = r"""
import json, os, time, pathlib
from repositories import database
database.configure(os.environ["DATABASE_URL"], force=True)
from domain import contracts as C
from domain.errors import AppError

def submit_command(request_id, session_id, content="跨进程并发测试输入", action=None):
    from services import command_service
    action = action or C.Action.clarify
    try:
        with database.session_scope() as db:
            receipt = command_service.submit_command(
                db,
                owner_email=os.environ["OWNER"],
                session_id=session_id,
                command=C.CommandRequest(
                    request_id=request_id,
                    action=action,
                    payload={"content": content},
                ),
            )
        return {
            "ok": True,
            "run_id": receipt.run_id,
            "reused": bool(receipt.reused),
            "session_version": receipt.session_version,
            "status": receipt.status,
        }
    except AppError as exc:
        return {"ok": False, "code": exc.code, "details": exc.details}

def wait_gate():
    gate = pathlib.Path(os.environ["GATE"])
    deadline = time.time() + 30
    while not gate.exists():
        if time.time() > deadline:
            raise SystemExit("gate timeout")
        time.sleep(0.002)
"""


class CrossProcessCase(unittest.TestCase):
    """每个用例独立数据库文件 + 独立临时目录（避免污染工作树）。"""

    def setUp(self) -> None:
        tmp_root = BACKEND / ".tmp_tests"
        tmp_root.mkdir(exist_ok=True)
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="xp_", dir=str(tmp_root)))
        self.db_path = self.tmpdir / "cross.db"
        self.db_url = "sqlite:///" + self.db_path.as_posix()
        database.configure(self.db_url, force=True)
        database.initialize_database()
        self.session_id = C.new_id()
        with database.session_scope() as db:
            session_repo.create_session(
                db,
                session_id=self.session_id,
                owner_email=OWNER,
                problem_statement="跨进程验收：预算协同工具",
                now_iso=C.utc_now_iso(),
            )

    def tearDown(self) -> None:
        database.dispose_engine()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- 子进程工具 -------------------------------------------------------- #

    def _child_env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": self.db_url,
                "PYTHONPATH": str(BACKEND),
                "PYTHONIOENCODING": "utf-8",
                "OWNER": OWNER,
                "SID": self.session_id,
                "GATE": str(self.tmpdir / "gate"),
            }
        )
        env.update({key: str(value) for key, value in extra.items()})
        return env

    def _spawn(self, body: str, **extra: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-c", _CHILD_PRELUDE + body],
            cwd=str(BACKEND),
            env=self._child_env(**extra),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _collect(self, procs: list[subprocess.Popen]) -> list[dict]:
        # 等两个子进程都就绪后再放行，制造真正的同时提交。
        time.sleep(0.2)
        (self.tmpdir / "gate").write_text("go", encoding="utf-8")
        results: list[dict] = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, f"子进程失败 rc={proc.returncode}\n{out}\n{err[-2000:]}")
            payloads = [line for line in out.splitlines() if line.strip().startswith("{")]
            self.assertTrue(payloads, f"子进程无 JSON 输出：{out}\n{err[-2000:]}")
            results.append(json.loads(payloads[-1]))
        return results

    # -- AC-06 ------------------------------------------------------------- #

    def test_same_request_id_in_two_processes_creates_one_run(self) -> None:
        request_id = C.new_request_id()
        body = (
            "wait_gate()\n"
            "print(json.dumps(submit_command(os.environ['REQ'], os.environ['SID']), ensure_ascii=False))\n"
        )
        procs = [self._spawn(body, REQ=request_id, WORKER="api-1"), self._spawn(body, REQ=request_id, WORKER="api-2")]
        first, second = self._collect(procs)

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["run_id"], second["run_id"], "同 request_id 必须复用同一个 Run")
        self.assertEqual(sorted([first["reused"], second["reused"]]), [False, True])

        with database.session_scope() as db:
            runs = run_repo.active_run_for_session(db, self.session_id)
            self.assertIsNotNone(runs)
            total_runs = db.execute(
                __import__("sqlalchemy").text("SELECT COUNT(*) FROM runs WHERE session_id=:sid"),
                {"sid": self.session_id},
            ).scalar_one()
            self.assertEqual(total_runs, 1, "同键重放不得创建第二个 Run")
            messages = session_repo.list_messages(db, self.session_id)
            self.assertEqual([m.seq for m in messages], [1, 2], "只允许一条用户消息")

    # -- AC-07 ------------------------------------------------------------- #

    def test_concurrent_different_keys_same_session_gets_session_busy(self) -> None:
        body = (
            "wait_gate()\n"
            "print(json.dumps(submit_command(os.environ['REQ'], os.environ['SID']), ensure_ascii=False))\n"
        )
        procs = [
            self._spawn(body, REQ=C.new_request_id(), WORKER="api-1"),
            self._spawn(body, REQ=C.new_request_id(), WORKER="api-2"),
        ]
        results = self._collect(procs)

        ok = [item for item in results if item["ok"]]
        rejected = [item for item in results if not item["ok"]]
        self.assertEqual(len(ok), 1, f"必须只有一个成功占位：{results}")
        self.assertEqual(len(rejected), 1, f"另一个必须被拒绝：{results}")
        self.assertIn(rejected[0]["code"], {"SESSION_BUSY", "VERSION_CONFLICT"}, rejected[0])

        with database.session_scope() as db:
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            self.assertIsNotNone(session.active_run_id, "成功者必须占用 active_run_id")
            messages = session_repo.list_messages(db, self.session_id)
            self.assertEqual(len(messages), 2, "被拒绝的请求不得写入用户消息")
            total_runs = db.execute(
                __import__("sqlalchemy").text("SELECT COUNT(*) FROM runs WHERE session_id=:sid"),
                {"sid": self.session_id},
            ).scalar_one()
            self.assertEqual(total_runs, 1)

    # -- AC-08 ------------------------------------------------------------- #

    def _advance_to_brainstorming(self) -> None:
        """用同步命令把产品定义填全并确认，会话进入 brainstorming（不触发模型）。"""
        command_service = __import__("services.command_service", fromlist=["submit_command"])
        with database.session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
            command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=self.session_id,
                command=C.CommandRequest(
                    request_id=C.new_request_id(),
                    action=C.Action.revise_brief,
                    payload={
                        "expected_product_version": state.version,
                        "fields": {
                            "target_users": {"value": "中小企业财务团队"},
                            "usage_scenario": {"value": "月度预算协同与审批"},
                            "core_problem": {"value": "预算表版本混乱、审批链缺失"},
                            "success_criteria": {"value": "月度预算编制时间缩短 40%"},
                        },
                    },
                ),
            )
        with database.session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
            command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=self.session_id,
                command=C.CommandRequest(
                    request_id=C.new_request_id(),
                    action=C.Action.confirm_brief,
                    payload={"expected_product_version": state.version, "fields": {}},
                ),
            )

    def test_two_worker_processes_claim_the_same_run_once(self) -> None:
        # brainstorm 在 M1 未实现工作流 → 不调用模型即可走完 claim/step/终结链路。
        self._advance_to_brainstorming()
        command_service = __import__("services.command_service", fromlist=["submit_command"])
        with database.session_scope() as db:
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            self.assertEqual(session.phase, C.SessionPhase.brainstorming.value)
            receipt = command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=self.session_id,
                command=C.CommandRequest(
                    request_id=C.new_request_id(),
                    action=C.Action.brainstorm,
                    payload={"content": "请四个角色给方案"},
                ),
            )
        run_id = receipt.run_id

        body = (
            "from workers.run_worker import RunWorker\n"
            "wait_gate()\n"
            "worker = RunWorker(worker_id=os.environ['WORKER'], interval=0.05, max_runs=1)\n"
            "worker.run(once=True)\n"
            "print(json.dumps({'processed': worker.processed}))\n"
        )
        procs = [self._spawn(body, WORKER="xp-worker-1"), self._spawn(body, WORKER="xp-worker-2")]
        results = self._collect(procs)

        self.assertEqual(sum(item["processed"] for item in results), 1, f"同一 Run 只能被领取一次：{results}")

        with database.session_scope() as db:
            self.assertIsNone(
                run_repo.active_run_for_session(db, self.session_id), "Run 已被唯一 Worker 执行并释放占位"
            )
            # 同步命令也会各建一条已终结 Run；这里只校验 brainstorm 的那一条。
            events = run_repo.events_after(db, run_id)
            started = [e for e in events if e.type == "run.started"]
            self.assertEqual(len(started), 1, "run.started 必须恰好一条")
            terminals = [e for e in events if e.type in C.TERMINAL_EVENT_TYPES]
            self.assertEqual(len(terminals), 1, "终结事件必须恰好一条")
            steps = run_repo.list_steps(db, run_id)
            self.assertEqual(len(steps), 1, "步骤不得重复创建")
            self.assertEqual(steps[0].attempt_count, 1, "不得重复尝试（无可重试的模型调用）")
            row = run_repo.get_run(db, run_id)
            self.assertIsNone(row.lease_owner)
            self.assertEqual(row.lease_epoch, 1, "只被领取一次 → epoch=1")
            self.assertEqual(
                row.status, C.RunStatus.failed.value, "M1 未实现 brainstorm workflow → 明确失败而非假成功"
            )
            self.assertEqual(row.error_code, "FEATURE_NOT_READY")

    # -- AC-28 ------------------------------------------------------------- #

    def test_auth_code_and_rate_limit_are_shared_across_processes(self) -> None:
        import hmac as _hmac

        code_hash = "hash-" + "a" * 40
        now_epoch = int(time.time())
        with database.session_scope() as db:
            auth_repo.issue_code(
                db,
                email=OWNER,
                code_hash=code_hash,
                ttl_seconds=600,
                now_epoch=now_epoch,
            )
            allowed = [
                auth_repo.hit_rate_limit(
                    db, scope="send-code", key="203.0.113.9", limit=2, window_seconds=60, now_epoch=now_epoch
                )
                for _ in range(3)
            ]
        self.assertEqual(allowed, [True, True, False], "父进程先耗尽限流窗口（limit=2）")

        body = (
            "from repositories import auth_repo\n"
            "now = int(time.time())\n"
            "with database.session_scope() as db:\n"
            "    first = auth_repo.verify_code(db, email=os.environ['OWNER'], code_hash=os.environ['CODE_HASH'], now_epoch=now)\n"
            "    second = auth_repo.verify_code(db, email=os.environ['OWNER'], code_hash=os.environ['CODE_HASH'], now_epoch=now)\n"
            "    limited = auth_repo.hit_rate_limit(db, scope='send-code', key='203.0.113.9', limit=2, window_seconds=60, now_epoch=now)\n"
            "print(json.dumps({'first': first, 'second': second, 'limited': limited}))\n"
        )
        proc = self._spawn(body, CODE_HASH=code_hash)
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, f"{out}\n{err[-2000:]}")
        payload = json.loads([ln for ln in out.splitlines() if ln.strip().startswith("{")][-1])

        self.assertTrue(payload["first"], "进程 B 必须能验证进程 A 写入的验证码")
        self.assertFalse(payload["second"], "验证码一次性：进程 B 内二次校验必须失败")
        self.assertFalse(payload["limited"], "限流计数跨进程共享：父进程已用满窗口")

        # 父进程再验证一次也必须失败（已被子进程消费）
        with database.session_scope() as db:
            again = auth_repo.verify_code(db, email=OWNER, code_hash=code_hash, now_epoch=int(time.time()))
        self.assertFalse(again)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
