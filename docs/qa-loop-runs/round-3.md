# Round 3 — 71 / 100

- 时间：2026-11-19 11:37（评分）
- 结论：**不通过**。无 P0；2 条 P1（UX-01 首屏二次提交 clarify、UX-02 审计收尾失败不可恢复）仍未修复，另有 3 条 P2 与 1 条数据一致性缺口。
- 交叉证据（evaluator 自跑）：后端 `Ran 506 tests / OK`；`npx tsc --noEmit` exit 0；`npm test` 115/115 pass；`npm run build` 7 路由；`npm run test:e2e` 1 pass（真起 uvicorn 127.0.0.1:58947）。

| 维度 | 得分 | 满分 |
| --- | --- | --- |
| 功能正确性 | 18 | 25 |
| 数据一致性与状态机 | 15 | 20 |
| 错误处理与边界 | 10.5 | 15 |
| 前端逻辑与交互 | 9 | 15 |
| 架构与代码质量 | 7.5 | 10 |
| 自动化测试 | 7.5 | 10 |
| 文档与可运维 | 3.5 | 5 |

## 发现

- **UX-01（P1）首屏二次提交 clarify**：`frontend/app/session/[id]/page.tsx:46-56` 在 `loadSession` 后读 `?problem` 并 `sendToCoach`，而 `frontend/app/page.tsx:60` 的 `router.push` 始终携带 `problem`；后端 `api/v2/common.py:397-429` 已在同一事务写 `seq=1` 人类消息并 `start_initial_run`（`command_service.py:743-778`），重复提交必撞 `_ensure_no_active_run`（`:293-300`）。
- **UX-02（P1）审计收尾失败不可恢复**：`backend/workflows/audit.py:1588-1594` 的 `except AppError: return _failed(...)` 未传 `session_phase` / `apply`，`_failed`（`:558-577`）默认不改 audit 状态 → `closing → failed`（`domain/state_machine.py:193`）成为死转移；前端 `retry-failed` 通道已存在却只在 `ContinuationNotice` 上挂载。
- **数据一致性**：预算账本跨 epoch 重置（`workflows/runner.py:1121` 只恢复限额，usage 仅在终结落库 `run_repo.py:310`，租约接管后计数从 0 起，可突破 10 次上限）；软删仅置 `deleted_at`（`session_repo.py:185`）。
- **P2**：UX-03 `NavButtons` / `resolveAuditEntryState` 不校验 `v2Phase`；UX-04 coach「先确认已有内容」文案与 `BriefCard` 禁用态互斥；UX-05 消息只取最近 100 条且无「加载更早」入口。
- **AC-40** 零实现；`RETRY_AUDIT_CONTEXT_CHANGED` 两个分支零测试。
- 文档失真：`docs/v4-acceptance-report.md:74` 的 AC-16 仍标「未实现」，但 `backend/workflows/synthesis.py` + `tests/test_synthesis.py`（8 例）已实现并注册于 `workflows/runner.py:93`。

## 本轮修复

- 首屏只 `loadSession`，不再重发 `?problem`（`app/session/[id]/page.tsx`）。
- 审计收尾失败写 `failed` 状态并给「重试收尾」入口（`audit.py` 传 `session_phase=auditing` + `apply=_apply_closing_failure`；前端 closing 允许重试）。
- 预算账本从 `runs.usage_json` 恢复已用额度后再累计。
- `end_audit` 后取消优先、`RETRY_AUDIT_CONTEXT_CHANGED` 补返回值语义。

## 复测

- 后端 506 OK；前端 115 pass、tsc 0、build 7 路由、e2e 1 pass。
- 修复规模：累计 28 文件 +1941/−183。