"""AC / 运行期中间载荷保留策略的回归（第 26 批）。

覆盖点：
* 只清「终态 + 早于窗口」的 Run，窗口内的 Run 不动；
* 跳过被任何子 Run 引用为 ``parent_run_id`` 的父 Run（AC-14 重试要复用其 output_json）；
* 跳过 ``loops.latest_run_id`` 指向的 Run（Loop Studio 重试要读最近一轮产物）；
* 清完后 Step 行仍在、状态可查，且 NOT NULL 列没有写坏。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

import sys  # noqa: E402

sys.path.insert(0, str(BACKEND))

from domain import contracts as C  # noqa: E402
from repositories import database, run_repo, session_repo  # noqa: E402
from repositories.database import (  # noqa: E402
    LoopRow,
    RunRow,
    StepCandidateRow,
    StepRow,
)

OWNER = "payload-retention@example.com"


class PayloadRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = BACKEND / ".tmp_tests"
        root.mkdir(exist_ok=True)
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="payload_retention_", dir=str(root)))
        cls.db_url = "sqlite+pysqlite:///" + (cls.tmpdir / "retention.db").as_posix()
        database.configure(cls.db_url, force=True)
        database.initialize_database(drop_first=True)

    @classmethod
    def tearDownClass(cls) -> None:
        database.dispose_engine()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        # 每个用例重建 schema：保留策略的断言基于「本用例插入的行」，跨用例残留会让
        # 计数断言失真（直接按表删又会撞 runs→loops 的外键）。
        database.initialize_database(drop_first=True)

    # -- 夹具 --------------------------------------------------------------- #

    def _session(self, suffix: str) -> str:
        with database.session_scope() as db:
            row = session_repo.create_session(
                db, owner_email=OWNER, problem_statement=f"保留策略夹具 {suffix}"
            )
            return row.id

    def _run(
        self,
        session_id: str,
        *,
        run_id: str,
        status: C.RunStatus,
        finished_at: str | None,
        parent_run_id: str | None = None,
    ) -> None:
        with database.session_scope() as db:
            db.add(
                RunRow(
                    id=run_id,
                    session_id=session_id,
                    owner_email=OWNER,
                    request_id=f"req-{run_id}",
                    action="brainstorm",
                    input_hash="hash",
                    base_session_version=1,
                    base_product_version=1,
                    status=status.value,
                    parent_run_id=parent_run_id,
                    created_at="2020-01-01T00:00:00Z",
                    finished_at=finished_at,
                )
            )

    def _step_with_payload(self, run_id: str, step_id: str) -> None:
        with database.session_scope() as db:
            db.add(
                StepRow(
                    id=step_id,
                    run_id=run_id,
                    logical_key="brainstorm.generate",
                    kind="brainstorm",
                    status=C.StepStatus.succeeded.value,
                    attempt_count=1,
                    input_hash="hash",
                    input_snapshot_json='{"messages": [{"role": "user", "content": "很长的上下文"}]}',
                    output_json='{"result": {"claims": [{"text": "断言"}]}}',
                    created_at="2020-01-01T00:00:00Z",
                    finished_at="2020-01-01T00:00:01Z",
                )
            )
            # 候选行外键指向 steps.id：必须先 flush，否则插入顺序不确定会撞外键。
            db.flush()
            db.add(
                StepCandidateRow(
                    id=f"cand-{step_id}",
                    run_id=run_id,
                    step_id=step_id,
                    local_id="local-1",
                    kind="claim",
                    payload_json='{"text": "候选"}',
                    created_at="2020-01-01T00:00:00Z",
                )
            )

    def _payloads(self, step_id: str) -> tuple[str, str | None, int]:
        with database.session_scope() as db:
            step = db.get(StepRow, step_id)
            candidates = (
                db.query(StepCandidateRow)
                .filter(StepCandidateRow.step_id == step_id)
                .count()
            )
            return step.input_snapshot_json, step.output_json, candidates

    # -- 用例 --------------------------------------------------------------- #

    def test_only_old_terminal_runs_are_cleared(self) -> None:
        sid = self._session("mixed")
        self._run(sid, run_id="run-old", status=C.RunStatus.succeeded,
                  finished_at="2020-01-02T00:00:00Z")
        self._step_with_payload("run-old", "step-old")
        self._run(sid, run_id="run-recent", status=C.RunStatus.succeeded,
                  finished_at="2099-01-01T00:00:00Z")
        self._step_with_payload("run-recent", "step-recent")
        self._run(sid, run_id="run-retry-parent", status=C.RunStatus.partial_succeeded,
                  finished_at="2020-01-03T00:00:00Z")
        self._step_with_payload("run-retry-parent", "step-parent")
        self._run(sid, run_id="run-retry-child", status=C.RunStatus.succeeded,
                  finished_at="2020-01-04T00:00:00Z", parent_run_id="run-retry-parent")

        with database.session_scope() as db:
            stats = run_repo.prune_intermediate_payloads(
                db, before_iso="2021-01-01T00:00:00Z"
            )
        # 只有 run-old 有中间载荷（run-retry-child 没有 step，不计入）；run-retry-parent
        # 作为重试父 Run 必须保留，run-recent 在窗口内必须保留。
        self.assertEqual(stats["runs"], 1, stats)
        self.assertEqual(stats["steps"], 1, stats)
        self.assertEqual(stats["candidates"], 1, stats)

        # 老 Run：上下文与结果被清、候选被删，但 Step 行还在（可查状态与错误码）
        self.assertEqual(self._payloads("step-old"), ("{}", "{}", 0))
        with database.session_scope() as db:
            self.assertEqual(db.get(StepRow, "step-old").status, C.StepStatus.succeeded.value)
            self.assertIsNotNone(db.get(RunRow, "run-old"))
        # 窗口内 / 重试父 Run：原样保留
        self.assertIn("很长的上下文", self._payloads("step-recent")[0])
        self.assertIn("很长的上下文", self._payloads("step-parent")[0])
        self.assertEqual(self._payloads("step-parent")[2], 1)

    def test_loop_latest_run_is_preserved(self) -> None:
        sid = self._session("loop")
        self._run(sid, run_id="run-loop-latest", status=C.RunStatus.succeeded,
                  finished_at="2020-01-05T00:00:00Z")
        self._step_with_payload("run-loop-latest", "step-loop-latest")
        with database.session_scope() as db:
            db.add(
                LoopRow(
                    id="loop-1",
                    session_id=sid,
                    owner_email=OWNER,
                    scope="mvp",
                    status=C.LoopStatus.shipped.value,
                    current_round=3,
                    max_rounds=3,
                    target_score=85,
                    loop_version=1,
                    focus="保留策略",
                    latest_run_id="run-loop-latest",
                    created_at="2020-01-05T00:00:00Z",
                    updated_at="2020-01-05T00:00:00Z",
                )
            )
        self._run(sid, run_id="run-free", status=C.RunStatus.succeeded,
                  finished_at="2020-01-06T00:00:00Z")
        self._step_with_payload("run-free", "step-free")

        with database.session_scope() as db:
            stats = run_repo.prune_intermediate_payloads(
                db, before_iso="2021-01-01T00:00:00Z"
            )
        self.assertEqual(stats["runs"], 1, stats)
        self.assertIn("很长的上下文", self._payloads("step-loop-latest")[0])
        self.assertEqual(self._payloads("step-loop-latest")[2], 1)
        self.assertEqual(self._payloads("step-free")[0], "{}")

    def test_limit_bounds_single_batch(self) -> None:
        sid = self._session("limit")
        for index in range(3):
            run_id = f"run-limit-{index}"
            self._run(sid, run_id=run_id, status=C.RunStatus.succeeded,
                      finished_at=f"2020-01-0{index + 1}T00:00:00Z")
            self._step_with_payload(run_id, f"step-limit-{index}")

        with database.session_scope() as db:
            first = run_repo.prune_intermediate_payloads(
                db, before_iso="2021-01-01T00:00:00Z", limit=2
            )
        self.assertEqual(first["runs"], 2, first)
        with database.session_scope() as db:
            second = run_repo.prune_intermediate_payloads(
                db, before_iso="2021-01-01T00:00:00Z", limit=2
            )
        self.assertEqual(second["runs"], 1, second)
        with database.session_scope() as db:
            third = run_repo.prune_intermediate_payloads(
                db, before_iso="2021-01-01T00:00:00Z", limit=2
            )
        self.assertEqual(third, {"runs": 0, "steps": 0, "candidates": 0})


if __name__ == "__main__":
    unittest.main()
