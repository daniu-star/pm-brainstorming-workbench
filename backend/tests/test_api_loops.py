"""Loop Studio v2 读取端点契约测试（docs/loop-studio-interfaces.md §6）。

覆盖：

1. 会话闭环列表（最新在前、``limit`` 截断、非法 ``limit`` 422）；
2. 闭环快照字段与五类产物负载还原；
3. 单轮详情（五阶段状态 / 产物索引、越界 404）；
4. 产物详情（视图 + payload、跨闭环 404）；
5. 归属校验（他会话 403）与缺失 / 跨对象 404；
6. 软删会话后的列表与快照 404；
7. ``GET /runs/{run_id}/snapshot`` 在 ``run.loop_id`` 存在时带回 ``loop``
   （走真实 start_loop 命令链路）。

测试库固定落在 ``backend/.tmp_tests/<prefix>``（本机 TMP/TEMP 不可用），
结束时 ``dispose_engine()`` 并删除临时目录。
"""
from __future__ import annotations

import atexit
import gc
import os
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

import main
from core import auth
from core.config import settings
from domain import contracts as C
from domain.errors import ErrorCode
from repositories import database, loop_repo, run_repo, session_repo
from services import command_service

TEST_SECRET = "test-secret-key-that-is-longer-than-32-characters"

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_TMP_ROOT = _BACKEND_DIR / ".tmp_tests"

_TEMP_DIRS: list[str] = []


def _make_db_dir(prefix: str) -> str:
    """在本目录的 ``.tmp_tests`` 下建临时库目录（不用系统 TEMP）。"""

    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = tempfile.mkdtemp(prefix=prefix, dir=str(_TMP_ROOT))
    _TEMP_DIRS.append(path)
    return path


def _cleanup_temp_dirs() -> None:
    """先释放 SQLite 引擎（Windows 上不释放会锁文件），再删除临时目录。"""

    try:
        gc.collect()
        database.dispose_engine()
    except Exception:  # pragma: no cover - 清理路径容错
        pass
    for path in list(_TEMP_DIRS):
        for attempt in range(4):
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                break
            try:
                gc.collect()
                database.dispose_engine()
            except Exception:  # pragma: no cover - 清理路径容错
                pass
            time.sleep(0.1 * (attempt + 1))
    _TEMP_DIRS.clear()


atexit.register(_cleanup_temp_dirs)


def _auth_config() -> dict:
    return {
        "auth_secret_key": TEST_SECRET,
        "smtp_username": "noreply@example.com",
        "smtp_password": "test-password",
        "smtp_from": "noreply@example.com",
        "auth_code_expire_minutes": 10,
        "auth_code_resend_seconds": 60,
    }


# --------------------------------------------------------------------------- #
# 产物负载夹具：五类产物各一份，字段断言用常量引用
# --------------------------------------------------------------------------- #

SEED_MVP_TITLE = "周报生成器 MVP"
SEED_FEATURE_ID = "f1"
SEED_PROTOTYPE_ENTRY = "app.py"
SEED_SCORE_TOTAL = 82
SEED_ROUND_SCORE = 82


def _artifact_fixtures() -> list[tuple[C.LoopArtifactKind, str, dict]]:
    """返回 (kind, title, payload) 三元组，payload 为契约模型 ``model_dump``。"""

    spec = C.LoopMvpSpec(
        title=SEED_MVP_TITLE,
        summary="把散落的工作记录一键汇总成可发布的周报",
        features=[
            C.MvpFeature(
                id=SEED_FEATURE_ID,
                title="自动汇总",
                description="读取本周记录并生成草稿",
                acceptance="10 分钟内产出可发布周报",
                priority=C.FeaturePriority.must,
                effort=C.EffortSize.m,
            )
        ],
        assumptions=[
            C.LoopAssumption(
                id="a1",
                statement="用户愿意每周粘贴工作记录",
                risk="记录格式不统一",
                verification="访谈 5 位目标用户",
            )
        ],
        not_in_scope=["多人协作编辑"],
    )
    validation = C.LoopValidation(
        checks=[
            C.ValidationCheck(
                id="v1",
                feature_id=SEED_FEATURE_ID,
                question="记录能否自动抓取？",
                result=C.CheckResult.unknown,
                notes="需要先确认数据来源",
            )
        ],
        summary="自动抓取路径仍未验证",
        unknowns=["自动抓取可行性", "模板可配置范围"],
    )
    prototype = C.PrototypeSpec(
        title="周报生成原型",
        summary="单文件脚本，输入记录输出 markdown",
        entry_file=SEED_PROTOTYPE_ENTRY,
        run_command="python app.py",
        files=[
            C.PrototypeFile(
                path=SEED_PROTOTYPE_ENTRY,
                language="python",
                content="print('weekly report')",
                purpose="入口脚本",
            )
        ],
        limitations="尚未接真实数据源",
        requires_human_build=True,
    )
    test_report = C.LoopTestReport(
        simulated=True,
        passed=1,
        cases=[
            C.LoopTestCase(
                id="t1",
                feature_id=SEED_FEATURE_ID,
                title="生成周报草稿",
                steps=["运行脚本", "检查输出文件"],
                expected="输出 markdown 草稿",
                status=C.TestCaseStatus.passed,
                evidence="console 输出符合预期",
            )
        ],
        notes="模拟执行，未接入真实数据",
    )
    scorecard = C.LoopScoreCard(
        total=SEED_SCORE_TOTAL,
        dimensions=[
            C.LoopScoreDimension(
                key=C.LoopScoreKey.functionality,
                score=20,
                max=25,
                reason="主流程可用",
            )
        ],
        verdict=C.LoopVerdict.iterate,
        blockers=["自动抓取尚未验证"],
        highlights=["产物结构完整"],
        next_actions=["补一次真实数据验证"],
        generated_by="model",
    )
    return [
        (C.LoopArtifactKind.mvp_spec, "MVP 方案", spec.model_dump(mode="json")),
        (C.LoopArtifactKind.validation, "验证清单", validation.model_dump(mode="json")),
        (C.LoopArtifactKind.prototype, "原型说明", prototype.model_dump(mode="json")),
        (C.LoopArtifactKind.test_report, "测试报告", test_report.model_dump(mode="json")),
        (C.LoopArtifactKind.scorecard, "评分卡", scorecard.model_dump(mode="json")),
    ]


class LoopsApiTestBase(unittest.TestCase):
    """共享临时库与签名 Cookie 的基类（不进 TestClient 的 with 块）。"""

    _tmpdir: str = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = _make_db_dir("api_loops_")
        db_path = (Path(cls._tmpdir) / "test.db").as_posix()
        database.reset_for_tests(f"sqlite+pysqlite:///{db_path}")

        cls._originals = {key: getattr(settings, key) for key in _auth_config()}
        for key, value in _auth_config().items():
            setattr(settings, key, value)

        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls) -> None:
        for key, value in cls._originals.items():
            setattr(settings, key, value)
        cls.client.close()

    # -- helpers ---------------------------------------------------------
    def setUp(self) -> None:
        self.email = f"tester-{uuid.uuid4().hex[:10]}@example.com"

    def headers(self, email: str | None = None, **extra: str) -> dict:
        token = auth.create_session_token(email or self.email)
        headers = {"Cookie": f"{settings.auth_cookie_name}={token}"}
        headers.update(extra)
        return headers

    def get(self, url: str, *, email: str | None = None, **kwargs):
        headers = self.headers(email)
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.get(url, headers=headers, **kwargs)

    def post(self, url: str, *, email: str | None = None, **kwargs):
        headers = self.headers(email)
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.post(url, headers=headers, **kwargs)

    def delete(self, url: str, *, email: str | None = None, **kwargs):
        headers = self.headers(email)
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.delete(url, headers=headers, **kwargs)

    def create_session(self, problem: str = "帮我验证一个面向独立开发者的周报工具") -> dict:
        resp = self.post("/api/v2/sessions", json={"problem_statement": problem})
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def cancel_initial_run(self, session_id: str) -> int:
        """取消创建会话时排入的 clarify Run，返回推进后的会话版本。"""

        with database.session_scope() as db:
            active = run_repo.active_run_for_session(db, session_id)
            self.assertIsNotNone(active, "新建会话应已排入首个 clarify Run")
            command_service.cancel_run(db, owner_email=self.email, run_id=active.id)
            session = session_repo.require_session(db, session_id, owner_email=self.email)
            return int(session.session_version)

    def seed_ready_brief(self, session_id: str) -> int:
        """模拟澄清已完成，让 confirm_brief 可执行；返回播种后的产品版本。"""

        with database.session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=self.email)
            state = session_repo.get_product_state(db, session_id)
            brief = state.brief.model_copy(deep=True)
            values = {
                "target_users": "独立开发者",
                "usage_scenario": "每周写项目周报",
                "core_problem": "手工整理耗时且容易遗漏",
                "success_criteria": "10 分钟内生成可发布的周报",
            }
            for name, value in values.items():
                field_obj = getattr(brief, name)
                setattr(
                    brief,
                    name,
                    field_obj.model_copy(
                        update={"value": value, "status": C.BriefFieldStatus.proposed}
                    ),
                )
            new_state = state.model_copy(update={"brief": brief, "version": state.version + 1})
            session_repo.save_product_state(db, session, new_state, now_iso=C.utc_now_iso())
            session_repo.touch_session(
                db, session, product_version=new_state.version, now_iso=C.utc_now_iso()
            )
            return new_state.version

    # -- loop 数据播种（直接用仓储写，模拟 Worker 已跑完一轮） --------------
    def seed_loop(
        self,
        session_id: str,
        *,
        now_iso: str = "2026-11-19T09:00:00Z",
        with_artifacts: bool = True,
        status: C.LoopStatus = C.LoopStatus.awaiting_decision,
        max_rounds: int = 3,
        target_score: int = 80,
    ) -> dict:
        """造一个 loop + 第 1 轮（五阶段 done + 五类产物），返回 loop_id / 产物 id。"""

        with database.session_scope() as db:
            loop = loop_repo.create_loop(
                db,
                session_id=session_id,
                owner_email=self.email,
                scope=C.LoopScope.mvp,
                max_rounds=max_rounds,
                target_score=target_score,
                focus="先做周报主流程",
                now_iso=now_iso,
            )
            loop_id = loop.id
            loop_repo.ensure_round(db, loop_id=loop_id, ordinal=1, now_iso=now_iso)
            for stage in C.LOOP_STAGES:
                loop_repo.record_stage(
                    db,
                    loop_id=loop_id,
                    ordinal=1,
                    stage=stage,
                    status=C.LoopStageStatus.done,
                    summary=f"{stage.value} 阶段已完成",
                    now_iso=now_iso,
                )
            artifacts: dict[str, str] = {}
            if with_artifacts:
                for kind, title, payload in _artifact_fixtures():
                    row = loop_repo.add_artifact(
                        db,
                        loop_id=loop_id,
                        ordinal=1,
                        kind=kind,
                        title=title,
                        payload=payload,
                        now_iso=now_iso,
                    )
                    artifacts[kind.value] = row.id
                round_row = loop_repo.get_round(db, loop_id=loop_id, ordinal=1)
                self.assertIsNotNone(round_row)
                loop_repo.update_round(
                    db,
                    round_row,
                    status=C.LoopRoundStatus.done,
                    score=SEED_ROUND_SCORE,
                    verdict=C.LoopVerdict.iterate,
                    finished_at=now_iso,
                )
            loop_repo.touch_loop(
                db,
                loop,
                status=status,
                loop_version=int(loop.loop_version) + 1,
                now_iso=now_iso,
            )
            return {"loop_id": loop_id, "artifacts": artifacts, "now_iso": now_iso}

    def session_version(self, session_id: str) -> int:
        with database.session_scope() as db:
            return int(
                session_repo.require_session(db, session_id, owner_email=self.email).session_version
            )

    def product_version(self, session_id: str) -> int:
        with database.session_scope() as db:
            return int(session_repo.get_product_state(db, session_id).version)


class SessionsLoopsListTests(LoopsApiTestBase):
    """GET /sessions/{session_id}/loops"""

    def test_list_returns_latest_first_and_clamps_limit(self) -> None:
        session = self.create_session()
        session_id = session["id"]
        older = self.seed_loop(session_id, now_iso="2026-11-19T09:00:00Z", with_artifacts=False)
        newer = self.seed_loop(session_id, now_iso="2026-11-19T10:00:00Z")

        resp = self.get(f"/api/v2/sessions/{session_id}/loops")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIsNone(body["next_cursor"])
        self.assertEqual([item["loop_id"] for item in body["items"]], [newer["loop_id"], older["loop_id"]])

        head = body["items"][0]
        self.assertEqual(head["session_id"], session_id)
        self.assertEqual(head["owner_email"], self.email)
        self.assertEqual(head["status"], C.LoopStatus.awaiting_decision.value)
        self.assertEqual(head["scope"], C.LoopScope.mvp.value)
        self.assertEqual(head["current_round"], 1)
        self.assertEqual(head["max_rounds"], 3)
        self.assertEqual(head["target_score"], 80)
        self.assertEqual(head["loop_version"], 2)
        self.assertEqual(head["created_at"], "2026-11-19T10:00:00Z")
        # 有轮次行的 loop 快照应带 1 轮（无产物时阶段为空，轮次含五类产物）
        self.assertEqual(len(head["rounds"]), 1)
        self.assertEqual(head["rounds"][0]["ordinal"], 1)
        self.assertIsNotNone(head["mvp_spec"])
        self.assertEqual(len(head["rounds"][0]["artifacts"]), 5)

        # 没有产物的 loop：快照字段为 null，轮次里没有产物索引
        tail = body["items"][1]
        self.assertEqual(len(tail["rounds"]), 1)
        self.assertEqual(tail["rounds"][0]["artifacts"], [])
        self.assertIsNone(tail["mvp_spec"])

        limited = self.get(f"/api/v2/sessions/{session_id}/loops", params={"limit": 1})
        self.assertEqual(limited.status_code, 200, limited.text)
        self.assertEqual(len(limited.json()["items"]), 1)
        self.assertEqual(limited.json()["items"][0]["loop_id"], newer["loop_id"])

        capped = self.get(f"/api/v2/sessions/{session_id}/loops", params={"limit": 500})
        self.assertEqual(capped.status_code, 200, capped.text)

        invalid = self.get(f"/api/v2/sessions/{session_id}/loops", params={"limit": 0})
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(invalid.json()["error"]["code"], ErrorCode.INVALID_REQUEST)

    def test_list_empty_and_unknown_session(self) -> None:
        session = self.create_session()
        body = self.get(f"/api/v2/sessions/{session['id']}/loops").json()
        self.assertEqual(body["items"], [])
        self.assertIsNone(body["next_cursor"])

        missing = self.get("/api/v2/sessions/" + "0" * 32 + "/loops")
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["error"]["code"], ErrorCode.NOT_FOUND)


class LoopDetailTests(LoopsApiTestBase):
    """GET /loops/{loop_id}"""

    def test_detail_restores_status_round_and_all_artifacts(self) -> None:
        session = self.create_session()
        seeded = self.seed_loop(session["id"], now_iso="2026-11-19T09:00:00Z")
        loop_id = seeded["loop_id"]

        resp = self.get(f"/api/v2/loops/{loop_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        snapshot = resp.json()
        self.assertEqual(snapshot["loop_id"], loop_id)
        self.assertEqual(snapshot["session_id"], session["id"])
        self.assertEqual(snapshot["owner_email"], self.email)
        self.assertEqual(snapshot["status"], C.LoopStatus.awaiting_decision.value)
        self.assertEqual(snapshot["scope"], C.LoopScope.mvp.value)
        self.assertEqual(snapshot["current_round"], 1)
        self.assertEqual(snapshot["max_rounds"], 3)
        self.assertEqual(snapshot["target_score"], 80)
        self.assertEqual(snapshot["loop_version"], 2)
        self.assertEqual(snapshot["focus"], "先做周报主流程")
        self.assertEqual(snapshot["created_at"], "2026-11-19T09:00:00Z")

        rounds = snapshot["rounds"]
        self.assertEqual(len(rounds), 1)
        round_view = rounds[0]
        self.assertEqual(round_view["ordinal"], 1)
        self.assertEqual(round_view["status"], C.LoopRoundStatus.done.value)
        self.assertEqual(round_view["score"], SEED_ROUND_SCORE)
        self.assertEqual(round_view["verdict"], C.LoopVerdict.iterate.value)
        self.assertEqual(round_view["started_at"], "2026-11-19T09:00:00Z")
        self.assertEqual(round_view["finished_at"], "2026-11-19T09:00:00Z")
        self.assertEqual([stage["stage"] for stage in round_view["stages"]], [s.value for s in C.LOOP_STAGES])
        for stage in round_view["stages"]:
            self.assertEqual(stage["status"], C.LoopStageStatus.done.value)
            self.assertIn("阶段已完成", stage["summary"])
        self.assertEqual(
            sorted(item["kind"] for item in round_view["artifacts"]),
            sorted(kind.value for kind, _, _ in _artifact_fixtures()),
        )

        # 五类产物从最新产物还原回快照字段
        self.assertEqual(snapshot["mvp_spec"]["title"], SEED_MVP_TITLE)
        self.assertEqual(snapshot["mvp_spec"]["features"][0]["id"], SEED_FEATURE_ID)
        self.assertEqual(snapshot["mvp_spec"]["not_in_scope"], ["多人协作编辑"])
        self.assertEqual(snapshot["validation"]["unknowns"], ["自动抓取可行性", "模板可配置范围"])
        self.assertEqual(snapshot["validation"]["checks"][0]["result"], C.CheckResult.unknown.value)
        self.assertEqual(snapshot["prototype"]["entry_file"], SEED_PROTOTYPE_ENTRY)
        self.assertEqual(snapshot["test_report"]["total"], 1)
        self.assertEqual(snapshot["test_report"]["passed"], 1)
        self.assertEqual(snapshot["test_report"]["cases"][0]["status"], C.TestCaseStatus.passed.value)
        self.assertEqual(snapshot["scorecard"]["total"], SEED_SCORE_TOTAL)
        self.assertEqual(snapshot["scorecard"]["verdict"], C.LoopVerdict.iterate.value)
        self.assertEqual(snapshot["scorecard"]["blockers"], ["自动抓取尚未验证"])

    def test_detail_exposes_decision_note_and_closed_at(self) -> None:
        """AUD-03：决策备注与收口时间必须能从快照读回（前端 LoopSummary 依赖）。"""

        session = self.create_session()
        seeded = self.seed_loop(session["id"])
        with database.session_scope() as db:
            loop = loop_repo.get_loop(db, seeded["loop_id"])
            self.assertIsNotNone(loop)
            loop_repo.close_loop(
                db,
                loop,
                status=C.LoopStatus.shipped,
                decision=C.LoopDecisionKind.ship,
                note="先交付主流程",
                now_iso="2026-11-19T09:30:00Z",
            )
        snapshot = self.get(f"/api/v2/loops/{seeded['loop_id']}").json()
        self.assertEqual(snapshot["status"], C.LoopStatus.shipped.value)
        self.assertEqual(snapshot["decision"], C.LoopDecisionKind.ship.value)
        self.assertEqual(snapshot["decision_note"], "先交付主流程")
        self.assertEqual(snapshot["closed_at"], "2026-11-19T09:30:00Z")

    def test_detail_without_artifacts_is_null(self) -> None:
        session = self.create_session()
        seeded = self.seed_loop(session["id"], with_artifacts=False)
        snapshot = self.get(f"/api/v2/loops/{seeded['loop_id']}").json()
        for field in ("mvp_spec", "validation", "prototype", "test_report", "scorecard"):
            self.assertIsNone(snapshot[field], field)


class LoopRoundTests(LoopsApiTestBase):
    """GET /loops/{loop_id}/rounds/{ordinal}"""

    def test_round_view_exposes_five_stages_and_artifacts(self) -> None:
        session = self.create_session()
        seeded = self.seed_loop(session["id"])
        loop_id = seeded["loop_id"]

        resp = self.get(f"/api/v2/loops/{loop_id}/rounds/1")
        self.assertEqual(resp.status_code, 200, resp.text)
        view = resp.json()
        self.assertEqual(view["ordinal"], 1)
        self.assertEqual(view["status"], C.LoopRoundStatus.done.value)
        self.assertEqual(view["score"], SEED_ROUND_SCORE)
        self.assertEqual(view["verdict"], C.LoopVerdict.iterate.value)
        self.assertEqual(view["finished_at"], seeded["now_iso"])
        self.assertEqual([stage["stage"] for stage in view["stages"]], [s.value for s in C.LOOP_STAGES])
        self.assertEqual(
            sorted(item["kind"] for item in view["artifacts"]),
            sorted(kind.value for kind, _, _ in _artifact_fixtures()),
        )

    def test_out_of_range_round_is_404(self) -> None:
        session = self.create_session()
        seeded = self.seed_loop(session["id"])
        for ordinal in (2, 99):
            resp = self.get(f"/api/v2/loops/{seeded['loop_id']}/rounds/{ordinal}")
            self.assertEqual(resp.status_code, 404, resp.text)
            error = resp.json()["error"]
            self.assertEqual(error["code"], ErrorCode.NOT_FOUND)
            self.assertEqual(error["details"]["ordinal"], ordinal)


class LoopArtifactTests(LoopsApiTestBase):
    """GET /loops/{loop_id}/artifacts/{artifact_id}"""

    def test_artifact_detail_returns_view_and_payload(self) -> None:
        session = self.create_session()
        seeded = self.seed_loop(session["id"])
        loop_id = seeded["loop_id"]
        artifact_id = seeded["artifacts"]["mvp_spec"]

        resp = self.get(f"/api/v2/loops/{loop_id}/artifacts/{artifact_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        artifact = body["artifact"]
        self.assertEqual(artifact["id"], artifact_id)
        self.assertEqual(artifact["kind"], C.LoopArtifactKind.mvp_spec.value)
        self.assertEqual(artifact["title"], "MVP 方案")
        self.assertGreater(artifact["size_bytes"], 0)
        self.assertEqual(artifact["created_at"], seeded["now_iso"])
        payload = body["payload"]
        self.assertEqual(payload["title"], SEED_MVP_TITLE)
        self.assertEqual(payload["features"][0]["id"], SEED_FEATURE_ID)
        self.assertEqual(payload["features"][0]["effort"], C.EffortSize.m.value)

    def test_artifact_of_another_loop_is_404(self) -> None:
        session = self.create_session()
        first = self.seed_loop(session["id"], now_iso="2026-11-19T09:00:00Z")
        second = self.seed_loop(session["id"], now_iso="2026-11-19T10:00:00Z")
        foreign_artifact = second["artifacts"]["scorecard"]

        resp = self.get(f"/api/v2/loops/{first['loop_id']}/artifacts/{foreign_artifact}")
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertEqual(resp.json()["error"]["code"], ErrorCode.NOT_FOUND)

        missing = self.get(f"/api/v2/loops/{first['loop_id']}/artifacts/{C.new_id()}")
        self.assertEqual(missing.status_code, 404, missing.text)


class LoopOwnershipTests(LoopsApiTestBase):
    """归属校验：他会话 / 缺失闭环一律拒绝。"""

    def test_other_owner_gets_403_on_every_endpoint(self) -> None:
        other = f"other-{uuid.uuid4().hex[:8]}@example.com"
        session = self.create_session()
        seeded = self.seed_loop(session["id"])
        loop_id = seeded["loop_id"]
        artifact_id = seeded["artifacts"]["mvp_spec"]

        for url in (
            f"/api/v2/sessions/{session['id']}/loops",
            f"/api/v2/loops/{loop_id}",
            f"/api/v2/loops/{loop_id}/rounds/1",
            f"/api/v2/loops/{loop_id}/artifacts/{artifact_id}",
        ):
            resp = self.get(url, email=other)
            self.assertEqual(resp.status_code, 403, f"{url} -> {resp.text}")
            error = resp.json()["error"]
            self.assertEqual(error["code"], ErrorCode.FORBIDDEN)
            self.assertFalse(error["retryable"])

    def test_unknown_loop_is_404(self) -> None:
        session = self.create_session()
        for url in (
            f"/api/v2/loops/{C.new_id()}",
            f"/api/v2/loops/{C.new_id()}/rounds/1",
            f"/api/v2/loops/{C.new_id()}/artifacts/{C.new_id()}",
        ):
            resp = self.get(url)
            self.assertEqual(resp.status_code, 404, f"{url} -> {resp.text}")
            self.assertEqual(resp.json()["error"]["code"], ErrorCode.NOT_FOUND)
        self.assertTrue(session["id"])

    def test_soft_deleted_session_hides_loops(self) -> None:
        session = self.create_session()
        seeded = self.seed_loop(session["id"])

        deleted = self.delete(
            f"/api/v2/sessions/{session['id']}",
            headers={"Idempotency-Key": C.new_request_id()},
        )
        self.assertEqual(deleted.status_code, 202, deleted.text)
        self.assertIsNotNone(deleted.json().get("deleted_at"))

        listed = self.get(f"/api/v2/sessions/{session['id']}/loops")
        self.assertEqual(listed.status_code, 404, listed.text)
        self.assertEqual(listed.json()["error"]["code"], ErrorCode.NOT_FOUND)

        # 闭环本身还在，但它所属会话已软删 → 读取返回 404（不泄露已删会话内容）
        detail = self.get(f"/api/v2/loops/{seeded['loop_id']}")
        self.assertEqual(detail.status_code, 404, detail.text)
        self.assertEqual(detail.json()["error"]["code"], ErrorCode.NOT_FOUND)


class RunSnapshotLoopTests(LoopsApiTestBase):
    """GET /runs/{run_id}/snapshot 的 loop 字段（真实命令链路）。"""

    def _start_loop(self, session_id: str) -> dict:
        """confirm_brief → start_loop，返回 202 回执（含 loop_id / run_id）。"""

        session_version = self.cancel_initial_run(session_id)
        self.seed_ready_brief(session_id)
        confirm = self.post(
            f"/api/v2/sessions/{session_id}/commands",
            json={
                "request_id": C.new_request_id(),
                "action": C.Action.confirm_brief.value,
                "expected_session_version": session_version,
                "payload": {"expected_product_version": 2},
            },
        )
        self.assertEqual(confirm.status_code, 202, confirm.text)
        self.assertEqual(confirm.json()["status"], C.RunStatus.succeeded.value)

        start = self.post(
            f"/api/v2/sessions/{session_id}/commands",
            json={
                "request_id": C.new_request_id(),
                "action": C.Action.start_loop.value,
                "expected_session_version": self.session_version(session_id),
                "payload": {
                    "scope": C.LoopScope.mvp.value,
                    "max_rounds": 3,
                    "target_score": 85,
                    "focus": "先做周报主流程",
                    "expected_product_version": self.product_version(session_id),
                },
            },
        )
        self.assertEqual(start.status_code, 202, start.text)
        return start.json()

    def test_snapshot_embeds_loop_for_loop_run(self) -> None:
        session = self.create_session()
        session_id = session["id"]
        receipt = self._start_loop(session_id)
        loop_id = receipt.get("loop_id")
        self.assertIsNotNone(loop_id, receipt)
        self.assertEqual(receipt["action"], C.Action.start_loop.value)
        run_id = receipt["run_id"]

        snapshot = self.get(f"/api/v2/runs/{run_id}/snapshot")
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        body = snapshot.json()
        self.assertEqual(body["run_id"], run_id)
        self.assertEqual(body["action"], C.Action.start_loop.value)
        self.assertEqual(body["loop_id"], loop_id)
        self.assertIsNotNone(body["loop"])
        loop = body["loop"]
        self.assertEqual(loop["loop_id"], loop_id)
        self.assertEqual(loop["session_id"], session_id)
        self.assertEqual(loop["status"], C.LoopStatus.running.value)
        self.assertEqual(loop["current_round"], 1)
        self.assertEqual(loop["max_rounds"], 3)
        self.assertEqual(loop["target_score"], 85)
        self.assertEqual(loop["loop_version"], 2)
        self.assertEqual(len(loop["rounds"]), 1)
        self.assertEqual(loop["rounds"][0]["ordinal"], 1)
        self.assertEqual(loop["rounds"][0]["status"], C.LoopRoundStatus.running.value)

        listed = self.get(f"/api/v2/sessions/{session_id}/loops").json()["items"]
        self.assertEqual([item["loop_id"] for item in listed], [loop_id])

    def test_snapshot_without_loop_leaves_fields_empty(self) -> None:
        session = self.create_session()
        with database.session_scope() as db:
            run = run_repo.active_run_for_session(db, session["id"])
            self.assertIsNotNone(run)
            run_id = run.id
        body = self.get(f"/api/v2/runs/{run_id}/snapshot").json()
        self.assertIsNone(body["loop_id"])
        self.assertIsNone(body["loop"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
