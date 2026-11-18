# V4 里程碑 M1 接口契约（团队共享）

> 本文件是并行开发的**唯一接口真相**。开工前必读；任何签名变更必须先消息通知 Lead。
> PRD 原件：`C:\Users\13067\.dsh\attachments\v1\files\0b\0b7fe483e5711099b1798b79e69e27a166a23c35ca34e79625a80280558ad04e\2026-11-18-pm-brainstorm-agent-architecture-prd-v4.md`
> （仓库内副本：`docs/superpowers/specs/2026-11-18-pm-brainstorm-agent-architecture-prd-v4.md`）

## 0. 环境与命令

```bash
# 全部命令在 Git Bash 下执行；cwd = C:/Users/13067/Desktop/pm-brainstorming-workbench
PY=C:/Users/13067/Desktop/pm-brainstorming-workbench/.venv/Scripts/python.exe
cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests -v   # PRD §11.1 要求的命令
```

- 已安装：fastapi 0.115.6 / pydantic 2.10.4 / pydantic-settings / SQLAlchemy 2.1.9 / alembic / openai 1.59.8 / httpx / pytest / greenlet / markupsafe / Mako。
- **不要尝试 pip install**：本机 pip 装包会挂起（临时目录问题）。如确有需要，先消息 Lead。前端 `npm install` 不可用（registry.npmjs.org 不通），只能用既有 `frontend/node_modules`。
- 不要执行 `git` 写操作（branch/commit/reset）；Lead 统一提交。
- 只改分配给自己的文件；`backend/domain/*`、`backend/repositories/database.py` 由 Lead 维护，需要改动先消息 Lead。

## 1. 目录与归属（M1）

| 路径 | 负责人 | 说明 |
|---|---|---|
| `backend/domain/{errors,contracts,state_machine}.py` | Lead | 已完成，勿改 |
| `backend/repositories/database.py` | Lead | 已完成，勿改 |
| `backend/repositories/session_repo.py` | storage | 会话/消息/产品状态/证据 |
| `backend/repositories/run_repo.py` | storage | Run/Step/Event/幂等/租约/候选 ID |
| `backend/repositories/audit_repo.py` | storage | Audit/AuditQuestion/评估修订/报告修订/控制意图 |
| `backend/repositories/auth_repo.py` | storage | 验证码与限流（共享 DB） |
| `backend/core/llm_client.py` | exec | 关闭 SDK 内部重试、usage、超时、结构化 JSON |
| `backend/workflows/runner.py` | exec | Run 生命周期、租约续约、预算、步骤持久化、恢复 |
| `backend/workers/run_worker.py` | exec | `python -m workers.run_worker` |
| `backend/services/command_service.py` | Lead | 命令接收/幂等/版本校验/同步命令/结果提交事务 |
| `backend/api/v2/*`、`backend/api/errors.py`、`backend/api/sse.py` | api | v2 端点、SSE、错误体、旧路由 adapter |
| `backend/services/context_builder.py`、`backend/workflows/coach.py`、`backend/core/role_prompts.py` | coach | 上下文构建、教练工作流、版本化 prompt |
| `frontend/lib/{contracts.generated.ts,api.ts,sse.ts}`、`frontend/store/sessionStore.ts`、`frontend/components/*` | frontend | 隔离 + SSE + 确定性画布 |
| `backend/migrations/import_json_sessions.py` | storage | WP-02 迁移（可后置） |

## 2. 通用约定

- 时间：写入用 `domain.contracts.utc_now_iso()`（UTC `2026-11-18T03:00:00Z`）；租约/截止用 epoch 秒整数（`int(time.time())`）。
- ID：`domain.contracts.new_id()`（32 位小写 hex）。候选 ID 也必须 32 hex。
- 事务：`repositories.database.session_scope()` 包一个短事务；**事务内禁止**任何模型/HTTP 调用。
- 错误：领域层只抛 `domain.errors.AppError(code, message, http_status=?, retryable=?, details=?)`。
- 分页：消息 `limit` 默认 50、上限 100；会话默认 20、上限 100；`before_seq` 取更早消息，返回按 seq 升序。
- JSON 列：读写用 `domain.contracts.canonical_json()` / `json.loads`。

## 3. 仓储层签名（storage 实现，其他人按此调用）

### 3.1 `repositories/session_repo.py`

```python
UNSET: object  # 哨兵：区分"不改"与"置 None"

# 会话
create_session(db, *, session_id, owner_email, problem_statement, now_iso) -> SessionRow
    # 同一事务内：sessions 行(phase=clarifying, version=1, next_message_seq=1/2)
    # + 初始人类消息(seq=1, sender_kind=human, stage=clarify)
    # + product_states v1 空 Brief + 事件不需要（未创建 run）
get_session(db, session_id, *, include_deleted=False) -> SessionRow | None
require_session(db, session_id, *, owner_email, include_deleted=False) -> SessionRow
    # 不存在 -> AppError(NOT_FOUND)；owner 不符 -> AppError(FORBIDDEN)
list_sessions(db, owner_email, *, cursor: str | None, limit: int) -> tuple[list[SessionRow], str | None]
    # 按 (updated_at DESC, id DESC) 稳定排序；cursor 为上一页最后一条 id；返回 (rows, next_cursor)
soft_delete_session(db, session, *, now_iso) -> None   # 置 deleted_at
touch_session(db, session, *, now_iso, phase=UNSET, session_version=UNSET, product_version=UNSET,
              active_run_id=UNSET, current_audit_id=UNSET, current_control_id=UNSET,
              brief_status=UNSET) -> SessionRow       # 写入后 session 对象同步更新
bump_session_version(db, session) -> int               # +1 并返回新值

# 消息
allocate_message_seq(db, session) -> int               # UPDATE sessions SET next_message_seq=next_message_seq+1，返回旧值
add_message(db, session, *, sender_kind, stage, content, status="completed",
            role_code=None, run_id=None, audit_id=None, message_id=None, now_iso) -> MessageRow
get_message(db, session_id, message_id) -> MessageRow | None
list_messages(db, session_id, *, before_seq=None, limit=50, stage=None, audit_id=None) -> list[MessageRow]
    # limit 超上限截断到 100；返回 seq 升序

# 产品状态（快照只增不改）
get_product_state(db, session_id, version: int | None = None) -> ProductState   # None=最新；无行时返回 initial_product_state
save_product_state(db, session, state: ProductState, *, now_iso) -> ProductState
    # 插入新 version 行（version 由调用方给，必须 > 当前）；同步 session.current_product_version

# 证据
add_evidence(db, session, *, kind, title, excerpt, source_uri=None, source_date=None,
             limitations=None, created_by=None, now_iso) -> EvidenceRow   # 计算 content_hash
get_evidence(db, evidence_id) -> EvidenceRow | None
list_evidence(db, session_id, *, limit=50) -> list[EvidenceRow]
```

### 3.2 `repositories/run_repo.py`（**已由 Lead 落地，以下为实际签名，以代码为准**）

```python
ACTIVE_RUN_STATUSES = (queued, running, interrupted, cancel_requested)

create_run(db, *, session_id, owner_email, request_id, action, input_hash,
           base_session_version, base_product_version, budget=None, now_iso=None,
           deadline_at=None, queue_deadline_at=None, parent_run_id=None, audit_id=None,
           base_audit_version=None, question_id=None, run_id=None) -> RunRow
    # 同一事务内写 run.queued 事件（seq=1）
get_run(db, run_id) -> RunRow | None
require_run(db, run_id, *, owner_email=None) -> RunRow
active_run_for_session(db, session_id) -> RunRow | None
claim_next_run(db, *, worker_id, now_epoch, lease_seconds=30) -> RunRow | None
    # 原子领取：queued 优先，其次租约过期的 running/interrupted/cancel_requested
    # 成功：status=running, lease_owner=worker_id, lease_epoch+=1,
    #       lease_expires_at=now+lease, started_at 首次填写, deadline_at 首次填写,
    #       并 append run.started 事件（runner 不要再写一次）
renew_lease(db, run_row, *, worker_id, lease_epoch, now_epoch, lease_seconds=30) -> bool
release_run(db, run_row, *, worker_id, lease_epoch, status, error_code=None,
            error_message=None, result_product_version=None,
            warnings=None, usage=None, now_iso=None) -> RunRow
    # 租约不匹配 -> AppError(LEASE_LOST)
recover_expired_leases(db, *, now_epoch, now_iso=None) -> list[str]
requeue_interrupted(db, *, limit=10) -> list[str]
expire_queued_runs(db, *, now_epoch, now_iso=None) -> list[str]   # failed(QUEUE_WAIT_EXCEEDED)
count_running_runs(db) -> int
count_queued_runs(db, *, owner_email=None) -> int

# 步骤（传 run 行对象）
ensure_step(db, run_row, *, logical_key, kind, role_code=None, input_hash=None,
            input_snapshot=None, previous_step_id=None, now_iso=None) -> StepRow  # 幂等
get_step(db, step_id) -> StepRow | None
get_step_by_key(db, run_id, logical_key) -> StepRow | None
list_steps(db, run_id) -> list[StepRow]
start_step(db, step, *, now_iso=None) -> StepRow        # running, attempt_count+=1
finish_step(db, step, *, status, output=None, error_code=None,
            error_message=None, now_iso=None) -> StepRow  # 同状态重复调用为 no-op
reserve_candidate_id(db, *, run_id, step_id, local_id, kind, payload, now_iso=None) -> str

# 事件
append_event(db, *, session_id, run_id, type, payload=None, now_iso=None) -> EventRow
    # seq = MAX(seq)+1，唯一键冲突重试 3 次
events_after(db, run_id, *, after_seq=0, limit=200) -> list[EventRow]
latest_event_seq(db, run_id) -> int
message_snapshot_for_run(db, run_id, *, limit=100) -> list[MessageRow]

# 幂等（expires_at 为 epoch 秒整数，不是 ISO 字符串）
get_idempotency(db, owner_email, request_id) -> IdempotencyRow | None
save_idempotency(db, *, owner_email, request_id, action, resource_scope, input_hash,
                 response_status, response, related_run_id=None, related_control_id=None,
                 ttl_days=30, now_iso=None) -> IdempotencyRow
```

**schema 变更记录（Lead 已改 `database.py`）**：`steps` 增 `created_at`（NOT NULL）；`idempotency_records.expires_at` 改为 Integer（epoch 秒）。

### 3.3 `repositories/audit_repo.py`

```python
create_audit(db, *, session_id, product_version, now_iso, audit_id=None) -> AuditRow
get_audit(db, audit_id) -> AuditRow | None
require_audit(db, audit_id, *, owner_email) -> AuditRow
open_audit_for_session(db, session_id) -> AuditRow | None   # status IN (created,opening,active,closing)
latest_audit_for_session(db, session_id) -> AuditRow | None
update_audit(db, audit, *, now_iso, status=UNSET, question_count=UNSET, end_reason=UNSET,
             active_question_id=UNSET, pending_continuation=UNSET, close_requested=UNSET,
             closing_control_id=UNSET, audit_version=UNSET, dimension_states=UNSET,
             report=UNSET) -> AuditRow
add_question(db, audit, *, ordinal, dimension, issue_refs, question_text,
             question_message_id=None, now_iso) -> AuditQuestionRow
get_question(db, audit_id, question_id) -> AuditQuestionRow | None
questions_for_audit(db, audit_id) -> list[AuditQuestionRow]
answer_question(db, question, *, answer_message_id, now_iso) -> None
record_assessment(db, question, *, assessment, rationale, evidence_refs, now_iso) -> None
add_assessment_revision(db, *, audit_id, audit_version, dimension=None, question_id=None,
                        before: dict, after: dict, origin="human",
                        source_message_ids=(), evidence_refs=(), rationale="", now_iso) -> str
save_report_revision(db, *, audit_id, audit_version, report: dict, generated_by="model", now_iso) -> str
list_report_revisions(db, audit_id) -> list[AuditReportRevisionRow]
audit_to_snapshot(db, audit) -> AuditSnapshot   # 组装 dimension_states + report

# 控制意图
create_control_intent(db, *, session_id, audit_id, owner_email, request_id, action,
                      target_run_id=None, now_iso) -> ControlIntentRow
get_control(db, control_id) -> ControlIntentRow | None
get_control_by_request(db, owner_email, request_id) -> ControlIntentRow | None
pending_end_intent(db, audit_id) -> ControlIntentRow | None   # action=end_audit, status IN (accepted,scheduled)
update_control(db, control, *, status, related_run_id=UNSET, error_code=UNSET, now_iso) -> None
```

### 3.4 `repositories/auth_repo.py`

```python
issue_code(db, *, email, code_hash, ttl_seconds, now_epoch) -> None  # 使旧码失效
get_active_code(db, email, now_epoch) -> AuthCodeRow | None
record_attempt(db, row, now_epoch) -> int            # attempts+=1，返回新值
consume_code(db, row, now_epoch) -> None
hit_rate_limit(db, *, scope, key, limit, window_seconds, now_epoch) -> bool
    # 返回 True=允许（并计数）；False=超限。滑动窗口按 window_start 整窗口对齐。
```

## 4. 服务层签名（Lead 实现）

### 4.1 `services/command_service.py`

```python
def submit_command(db, *, owner_email, session_id, command: CommandRequest,
                   now_iso, now_epoch) -> RunReceipt | ControlReceipt | StartAuditRecovery
def cancel_run(db, *, owner_email, run_id, now_iso, now_epoch) -> RunView
def retry_run(db, *, owner_email, run_id, request_id, now_iso, now_epoch) -> RunReceipt
def apply_outcome(db, *, run_id, worker_id, lease_epoch, outcome: RunOutcome, now_iso) -> None
```

`RunOutcome`（dataclass，定义在 `services/command_service.py` 或 `workflows/runner.py` 均可，**exec 与 Lead 共同使用**）：

```python
@dataclass
class RunOutcome:
    status: RunStatus
    product_state: ProductState | None = None
    messages: list[OutgoingMessage] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)        # {"type": str, "payload": dict}
    result_product_version: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    session_phase: SessionPhase | None = None
    session_version_bump: bool = True
    apply: Callable[[Any], None] | None = None   # 在同一事务内执行审计等额外写入
```

### 4.2 `services/context_builder.py`（coach）

```python
def build_context(db, *, session_id, task_kind: TaskKind, role_code: RoleCode,
                  product_version: int, current_message_id: str | None = None,
                  audit_id: str | None = None, prompt_version: str = "v1",
                  max_input_tokens: int = MAX_CONTEXT_INPUT_TOKENS) -> ContextBundle
```
注入顺序见 PRD FR-02：角色协议 → 产品定义 → 方案/决定/未决 → 证据 → 审计问题 → 最近对话 → 当前输入；不得重复注入当前用户消息。

### 4.3 `workflows/coach.py`（coach）

```python
def run_clarify(ctx: StepContext) -> RunOutcome      # 教练提问/收束
def run_confirm(...) 不需要；confirm_brief 是同步命令，由 command_service 处理
def coach_question_plan(brief: ProductBrief, ...) -> dict   # 纯函数，便于单测
```

`StepContext` 由 runner 提供：

```python
@dataclass
class StepContext:
    db: Session
    run: RunRow
    session: SessionRow
    product_state: ProductState
    current_message: MessageRow | None
    task_kind: TaskKind
    llm: LLMClient
    budget: BudgetTracker      # 见 runner
    worker_id: str
    lease_epoch: int
    deadline_epoch: int
```

### 4.4 `core/llm_client.py`（exec 扩展，保留旧 API）

```python
class LLMUsage: prompt_tokens:int; completion_tokens:int; total_tokens:int; usage_source: Literal["provider","estimated"]
class LLMResult: text:str; usage:LLMUsage; model:str; latency_ms:int; attempts:int; finish_reason:str|None
class LLMClient:
    async def complete(self, *, messages: list[dict], max_output_tokens: int, timeout_seconds: float,
                       purpose: str, json_schema: dict | None = None) -> LLMResult
    # 关闭 SDK 内部重试（max_retries=0）；超时/429/5xx 抛 LLMUnavailableError(retryable=True)
    # 结构化：json_schema 不为空时走 response_format={"type":"json_schema",...}，不支持则回退 json_object
    # usage 缺失时按 len//3 估算并标 usage_source="estimated"
async def complete_text(...) / complete_json(...) 便捷包装
# 旧函数 llm_stream / llm_complete 必须保留（compat adapter 仍用）
```

## 5. API 层（api）

### 5.1 通用
- 所有新端点在 `backend/api/v2/`，前缀 `/api/v2`；Cookie 鉴权复用 `api.deps`（`require_user` / `require_session_owner`）。
- 错误：`AppError` → `{"error":{"code","message","retryable","request_id","details"}}`，HTTP 状态取 `AppError.http_status`。
- 写请求头：`Idempotency-Key`（可选，缺省用 body.request_id）与 body.request_id 不一致 → 422 INVALID_REQUEST。
- 响应：接收命令 → 202 `RunReceipt`；控制意图 → 202 `ControlReceipt`；已有审计恢复 → 200 `StartAuditRecovery`。

### 5.2 端点（全部 /api/v2）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sessions` | body: `{problem_statement}`；建会话返回 `SessionView`(201) |
| GET | `/sessions` | `?cursor=&limit=` → `{items:[SessionView], next_cursor}` |
| GET | `/sessions/{sid}` | `SessionView` |
| DELETE | `/sessions/{sid}` | `Idempotency-Key` 必需；软删；返回 `{session_id, deleted_at}` |
| GET | `/sessions/{sid}/messages` | `before_seq,limit,stage,audit_id` → `{items:[MessageView], next_before_seq}` |
| GET | `/messages/{mid}` | `MessageView` |
| GET | `/evidence/{eid}` | `Evidence` |
| GET | `/sessions/{sid}/product-state` | `?version=` → `ProductState` |
| POST | `/sessions/{sid}/commands` | `CommandRequest` → 202 回执 |
| GET | `/runs/{rid}` | `RunView` |
| GET | `/runs/{rid}/snapshot` | `RunSnapshot`（单事务） |
| GET | `/runs/{rid}/events` | SSE：`after_seq` 或 `Last-Event-ID`（取较大）；`id:` = event_seq；心跳 15s 注释；终结后关闭；游标越界 410/422 |
| POST | `/runs/{rid}/cancel` | `Idempotency-Key`；→ `RunView` |
| POST | `/runs/{rid}/retry-failed` | `Idempotency-Key` + body request_id → 202 `RunReceipt` |
| GET | `/sessions/{sid}/canvas` | `?product_version=` → `CanvasProjection`（M1 返回确定性投影，见 canvas_projector） |
| GET | `/audits/{aid}` | `AuditSnapshot` |
| GET | `/controls/{cid}` | `ControlReceipt` |

### 5.3 旧路由兼容（PRD §7.3）
`/api/session/*`、`/api/brainstorm/*`、`/api/canvas/*`、`/api/interview/*` 保留一个版本，但：
- 全部改为调用 `command_service` / 查询层（**不得**再走 `db.session_store`/`core.agent_loop` 写路径）；
- `/api/canvas/*` 返回确定性投影；`PUT /api/canvas/tree` → 409 CANVAS_IS_DERIVED；
- 旧 `phase` 映射：define/coach→clarifying，brainstorm→brainstorming，interview→auditing；
- 旧 SSE 事件（role_start/token/role_done/done）由 v2 事件翻译；`done` 只发一次。

## 6. 前端（frontend）
- `lib/contracts.generated.ts`：手写镜像 `domain/contracts.py` 的枚举与接口（M1 不做自动 codegen）。
- `lib/sse.ts`：SSE 解析器改为纯函数 `parseSSEChunk(buffer, chunk)` 返回 `{events, rest}`；支持 LF/CRLF/多行 data/注释/id/UTF-8 跨块；`done` 幂等 once guard。
- `store/sessionStore.ts`：以 `session_id + run_id` 隔离；消息按 message_id 去重、事件按 `run_id+event_seq` 去重；所有异步写回校验 `session_id`/generation/product_version；切换会话取消订阅但不取消服务器任务；登出清空。
- 画布：只消费 `product_state.updated` → GET `/canvas`；版本单调（旧版本不得覆盖新版本）。
- 测试：`npm run test`（若无 runner 则用 `node --test` 纯逻辑测试）、`npx tsc --noEmit`、`npm run build`。

## 7. 完成标准（每人自检）
1. 自己的模块 `python -c "import ..."` 可导入；新增/修改单测通过（`python -m unittest discover -s tests`）。
2. 无 TODO 占位实现、无 `except Exception: pass` 静默吞错、无明文密钥。
3. 汇报格式：改了哪些文件 / 新增哪些测试 / 实际命令与结果 / 已知限制 / 需要 Lead 决策的点。
4. 不预填"通过"：测试失败就如实写失败。