# Loop Studio 接口契约（v5，Lead 与三名协作 agent 的共同约定）

> 本文件是**唯一契约**：以代码为准的部分均已落盘（contracts / database / loop_repo / command_service）。
> 任何与本文件冲突的实现都要先改本文件，再改代码。

## 0. 环境与纪律

- Python：`../.venv/Scripts/python.exe`，在 `backend/` 下执行；测试命令 `../.venv/Scripts/python.exe -m unittest tests.<module> -v`。
- 前端：`frontend/` 下 `npx tsc --noEmit`、`npm test`、`npm run build`。
- **禁止**：`pip install` / `npm install` / 任何 `git` 写操作 / 杀 8001·3001 上的进程（产品栈正在跑一键启动版本）。
- **禁止**改别人的文件（见 §1 归属表）；禁止改 `backend/domain/contracts.py`、`backend/repositories/database.py`、`backend/repositories/loop_repo.py`、`backend/services/command_service.py`（Lead 所有）。
- 本机 `TMP/TEMP` 不可用，`tempfile.gettempdir()` 会回退到 cwd：测试必须显式指定临时目录（`backend/.tmp_tests/<prefix>`），并在 tearDown 里 `dispose_engine()` + 清理。

## 1. 归属表

| 区域 | 归属 | 文件 |
| --- | --- | --- |
| Lead（已落盘，勿改） | Lead | `backend/domain/contracts.py`、`backend/repositories/{database,loop_repo}.py`、`backend/services/command_service.py`、`docs/loop-studio-interfaces.md` |
| loop-core | agent A | `backend/services/loop_harness.py`、`backend/workflows/loop.py`、`backend/core/loop_prompts.py`、`backend/workflows/runner.py`（仅注册三行）、`backend/tests/test_loop_harness.py`、`backend/tests/test_loop_workflow.py`、`backend/tests/test_loop_runner.py` |
| api-dev | agent B | `backend/api/v2/loops.py`、`backend/api/v2/__init__.py`、`backend/api/v2/runs.py`（仅把 loop 注入 snapshot）、`backend/tests/test_api_loops.py` |
| frontend-dev | agent C | `frontend/app/session/[id]/loop/page.tsx`、`frontend/lib/loopView.ts`、`frontend/lib/api.ts`、`frontend/lib/contracts.generated.ts`、`frontend/store/sessionStore.ts`、`frontend/app/session/[id]/page.tsx`（仅入口链接）、`frontend/tests/loop-view.test.cjs`、`frontend/tests/loop-page.test.cjs`、`frontend/tsconfig.test.json` |

## 2. 领域契约（已落盘，直接 import）

`backend/domain/contracts.py` 新增：

- 枚举：`LoopStatus(running/awaiting_decision/shipped/stopped/failed)`、`LoopScope(mvp/optimize)`、`LoopStage(ideate/validate/develop/test/evaluate)`、`LoopStageStatus(pending/running/done/failed/skipped)`、`LoopRoundStatus(running/done/failed)`、`LoopVerdict(pending/pass_/iterate/blocked)`、`LoopDecisionKind(iterate/ship/stop)`、`LoopArtifactKind(mvp_spec/validation/prototype/test_report/scorecard)`、`CheckResult(unknown/pass_/fail)`、`TestCaseStatus(untested/passed/failed/skipped)`、`FeaturePriority(must/should/could)`、`EffortSize(S/M/L)`、`LoopScoreKey(functionality/feasibility/user_value/risk/delivery/testability/docs)`。
- 常量：`LOOP_STAGES`、`MAX_LOOP_ROUNDS=5`、`MIN_LOOP_ROUNDS=1`、`LOOP_ROUNDS_DEFAULT=3`、`LOOP_TARGET_SCORE_DEFAULT=85`、`LOOP_TARGET_SCORE_MIN=60`、`LOOP_TARGET_SCORE_MAX=100`、`MAX_LOOP_FEATURES=12`、`MAX_LOOP_ASSUMPTIONS=8`、`MAX_LOOP_FILES=12`、`MAX_LOOP_FILE_CHARS=6000`、`MAX_LOOP_CHECKS=12`、`MAX_LOOP_CASES=20`、`MAX_LOOP_DIMENSIONS=7`、`MAX_LOOP_BLOCKERS=8`、`LOOP_STAGE_OUTPUT_TOKENS=1500`、`LOOP_FINAL_OUTPUT_TOKENS=2200`、`LOOP_SCORE_LABELS`、`LOOP_SCORE_MAX`（25/15/20/15/10/10/5，合计 100）。
- 模型：`LoopAssumption(id/statement/risk/verification)`、`MvpFeature(id/title/description/acceptance/priority/effort)`、`LoopMvpSpec(title/summary/features/assumptions/not_in_scope)`（至少 1 个 feature、id 唯一）、`ValidationCheck(id/feature_id/question/result/evidence_refs/notes)`、`LoopValidation(checks/summary/unknowns)`、`PrototypeFile(path/language/content/purpose)`、`PrototypeSpec(title/summary/entry_file/run_command/files/limitations/requires_human_build)`、`LoopTestCase(id/feature_id/title/steps/expected/status/evidence)`、`LoopTestReport(total/passed/failed/skipped/simulated/cases/notes)`（total 自动对齐 cases 数）、`LoopScoreDimension(key/score/max/reason)`、`LoopScoreCard(total/dimensions/verdict/blockers/highlights/next_actions/generated_by(model|harness)/hard_capped)`、`LoopStageView`、`LoopArtifactView`、`LoopRoundView`、`LoopSnapshot`、`LoopAck`、`LoopUpdatedPayload(loop_id/status/current_round/loop_version/stage/stage_status/round_status/score/verdict/artifact_ids)`、`StartLoopPayload`、`LoopDecisionPayload`。
- `Action` 新增 `start_loop`、`loop_decision`（NORMAL_ACTIONS 13 个）与内部动作 `loop_round`（不入 NORMAL_ACTIONS，用户提交 → 422 UNKNOWN_ACTION）；`EventType` 新增 `loop_updated="loop.updated"`（第 12 种）；`TaskKind` 新增 `loop_round`；`RunReceipt.loop_id`、`RunSnapshot.loop/loop_id`、`SessionView.current_loop_id` 已加。

## 3. 数据与仓储（已落盘）

- 表：`loops`、`loop_rounds`（UNIQUE(loop_id, ordinal)）、`loop_stage_results`（UNIQUE(loop_id, round_ordinal, stage)）、`loop_artifacts`；`runs.loop_id`（含旧库 `ALTER TABLE` 加法升级）。
- `backend/repositories/loop_repo.py` 公开函数（**只 flush 不 commit**，事务由调用方控制）：
  - `create_loop(db,*,session_id,owner_email,scope,max_rounds,target_score,focus,latest_run_id,now_iso,loop_id)` → `LoopRow`
  - `get_loop/require_loop(owner_email 校验)/latest_loop_for_session/active_loop_for_session/list_loops`
  - `touch_loop(db,row,*,status/current_round/loop_version/target_score/max_rounds/latest_run_id/decision/decision_note/closed_at/now_iso)`、`bump_loop_version`、`close_loop(db,row,*,status,decision,note,now_iso)`
  - `ensure_round(db,*,loop_id,ordinal,run_id,now_iso)`（幂等）、`get_round/list_rounds/update_round(db,row,*,status,score,verdict,run_id,finished_at)`
  - `record_stage(db,*,loop_id,ordinal,stage,status,summary,payload,error_code,now_iso)`（upsert，payload 存模型 dump）、`get_stage/list_stages`、`stage_to_view`
  - `add_artifact(db,*,loop_id,ordinal,kind,title,payload,now_iso)`（同轮同 kind 先删后插）、`get_artifact/list_artifacts`
  - `loop_to_snapshot(db,row,*,include_rounds=True) -> LoopSnapshot`（从最新一轮的 artifacts 还原五个产物）、`loop_round_to_view`

## 4. 命令与 Run 排期（已落盘，勿改）

- `Action.start_loop` → 异步：建 loop（`status=running,current_round=1`）+ 第 1 轮 Run（`action=start_loop`，`TaskKind.loop_round`，`runs.loop_id`），占 `active_run_id`，bump `session_version`；已有 `running/awaiting_decision` 的 loop → `409 LOOP_ACTIVE`；`expected_product_version` 不匹配 → `VERSION_CONFLICT`。
- `Action.loop_decision`：`iterate` → 建下一轮 Run（`current_round+1`，超 `max_rounds` → `409 LOOP_ROUNDS_EXHAUSTED`）；`ship/stop` → **同步**：建一条 `succeeded` Run + 系统消息 + `loop.updated` 事件 + 在 `apply` 钩子里 `close_loop`。
- 状态错误：`LOOP_NOT_AWAITING_DECISION`(409)、`LOOP_VERSION_CONFLICT`(409)。
- **Run 预算里带闭环参数**（workflow 必须从这里读，不依赖进程内状态）：
  `loop_id`、`loop_round`（本轮序号）、`loop_scope`、`loop_max_rounds`、`loop_target_score`、`loop_focus`、`loop_stage_output_tokens`、`loop_final_output_tokens`；其余沿用 `default_budget()`（`max_upstream_calls_per_run=10`、`max_run_output_tokens=14000` 等）。

## 5. loop-core 交付规格

### 5.1 `backend/services/loop_harness.py`（纯函数、无 IO、无随机）

```python
def validate_mvp_spec(spec: LoopMvpSpec) -> list[str]          # blocker 文案；空列表=通过
def validate_validation(validation: LoopValidation, spec: LoopMvpSpec) -> list[str]
def validate_prototype(prototype: PrototypeSpec, spec: LoopMvpSpec) -> list[str]
def validate_test_report(report: LoopTestReport, spec: LoopMvpSpec) -> list[str]
def clamp_score(card: LoopScoreCard, *, blockers: list[str], p0: int, p1: int) -> LoopScoreCard
def derive_verdict(card: LoopScoreCard, *, target_score: int, round_ordinal: int, max_rounds: int) -> LoopVerdict
def feature_coverage(spec: LoopMvpSpec, *, validation=None, test_report=None) -> dict[str, dict]
```

- `clamp_score` 硬规则：`blockers` 非空或 `p0>=1` → `total=min(total,70)`；`p1>=3` → `min(total,85)`；标记 `hard_capped=True`。`total` 为各维度之和，维度 key 必须落在 `LOOP_SCORE_MAX`，`max` 取表值，缺维度按 0 计但记 blocker「缺少维度」。
- `derive_verdict`：`total>=target_score and 无 blocker` → `pass_`；`round_ordinal>=max_rounds` → `iterate`（轮次用尽仍不足，交由用户决定 ship/stop）；否则 `iterate`；`blockers>=3` → `blocked`。
- 不写库、不调模型、不读文件；纯用入参，确定性（同输入同输出）。

### 5.2 `backend/core/loop_prompts.py`

- `PROMPT_VERSION = "v5.0"`；`PURPOSE_IDEATE="loop.ideate"`、`PURPOSE_VALIDATE="loop.validate"`、`PURPOSE_DEVELOP="loop.develop"`、`PURPOSE_TEST="loop.test"`、`PURPOSE_EVALUATE="loop.evaluate"`。
- `build_stage_messages(ctx, *, stage, prior: dict) -> list[LLMMessage]`：一个 system（阶段职责+输出协议+硬约束），一个 user（产品定义 + 上阶段产物摘要 + 本轮 focus）。**只允许注入既有产物摘要，不得注入原始 JSON 流给用户**。
- 五阶段输出协议（模型必须返回严格 JSON）：
  1. `ideate` → `LoopMvpSpec`
  2. `validate` → `LoopValidation`
  3. `develop` → `PrototypeSpec`
  4. `test` → `LoopTestReport`（`simulated=True` 时必须在 notes 写「未实际执行」）
  5. `evaluate` → `LoopScoreCard`（`generated_by="model"`）
- 系统提示必须声明：不得编造 evidence_id；引用证据只能来自 `<evidence>` 段；不确定就标 unknown/unverified。

### 5.3 `backend/workflows/loop.py`

```python
async def run_loop_round(ctx) -> RunOutcome      # runner 注册名：TaskKind.loop_round -> ("workflows.loop","run_loop_round")
```

- 从 `ctx.run.loop_id` / `ctx.budget["loop_id"]`、`ctx.budget["loop_round"]` 取闭环与轮次；缺任一 → `RunOutcome(status=failed, error_code=ErrorCode.INVALID_REQUEST)`。
- 逐阶段执行：`loop.stage.ideate`、`loop.stage.validate`、`loop.stage.develop`、`loop.stage.test`、`loop.stage.evaluate`（`logical_key` 固定，`kind="loop_stage"`）。
  **staging 用独立短事务**：`with session_scope() as sdb:` 内 `ensure_step`+`start_step` → 完成阶段再开短事务 `finish_step`+`record_stage(...)`+`add_artifact(...)`（`from repositories.database import session_scope`；原因：runner 的 `ctx.db` 不 commit，中途崩溃要能回放已完成阶段）。
- 模型调用：`await ctx.llm.complete_json(messages=..., json_schema=<模型类>, max_output_tokens=<预算里的 stage tokens>, purpose=PURPOSE_*)` → `(parsed, LLMResult)`；`LLMError`/结构失败重试修复**最多一次**（把校验错误回喂，`parsed=None` 时抛 `MODEL_OUTPUT_INVALID` 由外层转 failed/partial）。
- 确定性校验：`ideate` 后跑 `validate_mvp_spec`，`validate` 后跑 `validate_validation`，`develop` 后 `validate_prototype`，`test` 后 `validate_test_report`；任一阶段 blocker 非空 → 该阶段 `status=failed`，但**继续执行后续阶段**（后续阶段能看到 blocker 摘要并修正），最终 `status=partial_succeeded`。
- `evaluate` 后：`clamp_score` + `derive_verdict`（`hard_capped` 覆盖模型给的分），把最终 `LoopScoreCard` 写 artifact；决策：
  - `verdict==pass_` → `loop.status=awaiting_decision`；`current_round` 不变；`loop_version+1`。
  - `verdict==iterate|blocked` 且 `round_ordinal < max_rounds` → `loop.status=running`（等用户 `loop_decision(iterate)` 再排下一轮；**不要自动起下一轮**）。
  - `round_ordinal >= max_rounds` → `awaiting_decision`，`LoopScoreCard.next_actions` 里追加「轮次已用尽，请决定交付或停止」。
- 产品状态：`product_state.version + 1`，`claims` 追加 must-have 功能的 `kind=feature`、`status=proposed`、`origin=model`、`created_product_version=新版本`、`source_message_ids=[本轮用户消息 id]`、`role_code=None`；`decisions` 追加一条 `DecisionStatus.proposed` 的「交付建议」；**绝不置 confirmed**；`unresolved_issues` 收 `blockers`（`kind="blocker"`）；`options` 收 must-have 功能（沿用 `OptionItem`，`status=proposed`）。
- 事件（`outcome.events`）：每阶段一条 `loop.updated`（`LoopUpdatedPayload(stage=..., stage_status=..., round_status=..., score=..., verdict=..., artifact_ids=[...])`），最后一条 `product_state.updated`（`product_version`+`session_version`）。**每 Run 恰好一个终结事件由 `_commit_outcome` 负责，workflow 不要自己发终结事件。**
- `outcome.apply` 钩子（在提交事务内执行）：`loop_repo.update_round(... status=done, score, verdict, finished_at)`、`loop_repo.touch_loop(... status=..., current_round, latest_run_id, loop_version+1)`、失败时 `update_round(status=failed)` + `touch_loop(status=failed)`。
- 返回 `RunOutcome(status=..., product_state=..., messages=[OutgoingMessage(display_text 摘要, sender_kind=agent, role_code=?…)], events=..., session_phase=None, brief_status=None, usage=...)`；`display_text` 只给一句中文摘要（例「第 1 轮闭环已完成：评分 82/100（待决策）」），**不渲染 JSON，不超过 6000 字**。
- 预算：总上游调用 ≤10（5 阶段 + 至多 2 次修复 + 预留）；不足 → `RunOutcome(status=failed, error_code=BUDGET_EXCEEDED)`，且**不写 product_state**。

### 5.4 `backend/workflows/runner.py`（只加注册）

```python
WorkflowRef = tuple[str, str]
# 在 WORKFLOWS 里追加：
TaskKind.loop_round: ("workflows.loop", "run_loop_round"),
```

若 `ACTION_TASK_KIND` 已含 `loop_round`（已落盘），则 `Action.start_loop/loop_decision` 的 Run 会自动分派；测试可用 `WORKFLOW_DISPATCH` 注入桩。

### 5.5 测试（三份文件，真跑绿）

- `test_loop_harness.py`：≥12 用例——spec 校验（空 features/重复 id/缺 acceptance）、validation 覆盖（must 未覆盖→blocker）、prototype 入口不存在/超长、test report 缺 must 用例/失败用例、`clamp_score`（blocker→70、p1≥3→85、无问题保持）、`derive_verdict`（达阈→pass_、轮次用尽→iterate、3 blocker→blocked）、`feature_coverage`、确定性（两次调用逐字节相等）。
- `test_loop_workflow.py`：假 LLM（`FakeLLM` 按 `purpose` 返回五阶段合法 JSON）→ 五阶段全成功：5 个 stage step `succeeded`、5 条 artifact、`product_state` v+1、`product_state.updated` 1 条、每阶段 `loop.updated`、`loop.status=awaiting_decision`、`loop_version` 递增、消息含摘要；另一用例 validate 阶段 blocker → `partial_succeeded` 且后续阶段仍执行；再一例 evaluate 硬上限（model 给 96 但 2 blocker → 70）。
- `test_loop_runner.py`：`WORKFLOW_DISPATCH` 注册后真 `run_repo` 领 Run + `execute_run`：事件序列（run.queued/run.started/step.started×6（runner 顶层 loop.round + 5 个阶段）/loop.updated×5/product_state.updated/run.completed）、单一终结事件、`runs.usage_json` 有 calls、`loop_rounds` 行 done + score、`loop_artifacts` 5 条。

## 6. api-dev 交付规格（`backend/api/v2/loops.py`）

全部走 `api/v2/common.py` 的 `require_user` / `open_db` / 错误体约定（`{"error":{code,message,retryable,request_id,details}}`）。

- `GET /api/v2/sessions/{session_id}/loops` → `{"items":[LoopSnapshot...], "next_cursor": null}`（默认最新 20，owner 校验，软删会话 404）。
- `GET /api/v2/loops/{loop_id}` → `LoopSnapshot`（owner 校验 → 403）。
- `GET /api/v2/loops/{loop_id}/rounds/{ordinal}` → `LoopRoundView`（越界 → 404）。
- `GET /api/v2/loops/{loop_id}/artifacts/{artifact_id}` → `{"artifact": LoopArtifactView, "payload": <model dump>}`（不在该 loop → 404）。
- `GET /api/v2/runs/{run_id}/snapshot`：当 `run.loop_id` 存在时填 `snapshot.loop`（`loop_repo.loop_to_snapshot`）与 `snapshot.loop_id`，其余字段不变（单事务）。
- 注册：`api/v2/__init__.py` 里 include_router，`main.py` 无需改（已挂 `/api/v2`）。
- 测试 `test_api_loops.py`：≥6 用例——列表/详情/轮次/产物 200 字段断言、他会话 403、不存在 404、软删会话 404、snapshot 带 loop（可先用 `loop_repo` 造数据 + `submit_command(start_loop)` 造 Run）。

## 7. frontend-dev 交付规格

- `frontend/lib/loopView.ts`（纯函数，node --test 可测）：`STAGE_LABELS/STAGE_HINTS`、`statusLabel`、`scoreLabel`、`formatScore(card)`、`stageProgress(round)`、`pickCurrentRound(snapshot)`、`decisionAvailability(snapshot)`（`iterate/ship/stop` 何时可用）、`blockerText(blockers)`、`artifactTitle(kind)`、`loopErrorText(code)`（`LOOP_ACTIVE/LOOP_NOT_AWAITING_DECISION/LOOP_ROUNDS_EXHAUSTED/VERSION_CONFLICT` 等中文）。
- `frontend/lib/api.ts`：`fetchSessionLoops(sessionId)`、`fetchLoop(loopId)`、`fetchLoopRound(loopId, ordinal)`、`fetchLoopArtifact(loopId, artifactId)`；`submitCommand` 已存在，决策用 `action="loop_decision"`、`action="start_loop"`。
- `frontend/store/sessionStore.ts`：`startLoop(opts)`（发 start_loop，`expected_product_version` 必传）、`decideLoop({loopId, expectedLoopVersion, decision, note})`、`hydrateLoop(snapshot)`、`hydrateLoopById(loopId)`；`finalizeRun` 结束时若 `run.loop_id` 存在 → `hydrateLoopById`；`loop.updated` 局部 payload 只在 `loop_id` 与当前一致时刷新。
- `frontend/app/session/[id]/loop/page.tsx`：**Loop 工作室**——阶段时间线（五阶段 + 状态色）、当前轮/总轮、评分卡（七维条 + 总分 + verdict 徽标 + blockers 列表）、五类产物 Tab（MVP 规格 / 检验 / 原型代码 / 测试报告 / 评分卡）、决策条（`iterate` 需 `current_round < max_rounds`；`ship/stop` 仅在 `awaiting_decision`）、`StartLoopBar`（未启动时 `max_rounds/target_score/focus` 表单；`409 LOOP_ACTIVE` → 跳到已有 loop）。
- `frontend/app/session/[id]/page.tsx`：加入口按钮（`brainstorming/audit_completed` 且非 legacy 显示「Loop 工作室」）。
- 测试：`loop-view.test.cjs`（≥12 断言，纯函数）、`loop-page.test.cjs`（≥6 用例，用 `fetch` 桩与 node 内置测试跑组件逻辑导出，不依赖 jsdom）；`tsconfig.test.json` 把两个新 lib/页面依赖加入编译面。
- 真实命令必须全绿：`npx tsc --noEmit`、`npm test`、`npm run build`。

## 8. 汇报格式

结束时回一条消息给 Lead：①改了哪些文件（逐个列出）②真实命令与结果（贴 `Ran N tests / OK`、`npx tsc`、`npm test` 的原始计数行）③偏差/未完成项（阻塞点写清）④需要 Lead 决策的接口问题。

## 9. 完成定义

- 后端 `python -m unittest discover -s tests` 全绿（Lead 最终统一跑）。
- 三个新增测试文件真实存在并覆盖上述断言，不得只用字符串存在性断言。
- 前端三命令全绿，页面能从真实 API 拉数据并渲染五阶段与评分卡。
