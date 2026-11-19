# Round 1 — 62 / 100

- 时间：2026-11-19 09:39（评分）
- 结论：**不通过**。存在未修 P0（审计完成后主循环断裂）+ 4 条 P1，按硬规则封顶 70。
- 基线：后端 496 用例全绿、前端 87 用例 + build/e2e 通过，但关键集成缺陷全部逃逸测试。

| 维度 | 得分 | 满分 |
| --- | --- | --- |
| 功能正确性 | 14 | 25 |
| 数据一致性与状态机 | 16 | 20 |
| 错误处理与边界 | 10 | 15 |
| 前端逻辑与交互 | 6 | 15 |
| 架构与代码质量 | 7 | 10 |
| 自动化测试 | 6 | 10 |
| 文档与可运维 | 3 | 5 |

## P0 / P1

- **P0**：`frontend/store/sessionStore.ts:86-98` 的 `mapV2Phase` 把 `auditing` 与 `audit_completed` 同映射为旧 `interview`，审计完成后输入被 `answerInterview` 吞掉（`frontend/components/chat/InputBox.tsx:27-37`）。
- **P0**：无待答问题时只 `setError`（`sessionStore.ts:1428-1432`），用户没有任何可操作出口。
- **P0**：全仓无「重新审计」入口，审计失败只能刷新页面。
- **P0**：AC-16（主持人 synthesis + 专家复核）未实现（`docs/v4-acceptance-report.md:74,104`）。
- **P1**：确认简报后没有「开始讨论」（`InputBox.tsx:76-94`、`MessageList.tsx:148`）。
- **P1**：`pending_continuation` 前端完全未消费 → 审计永久卡在「等待收尾」（`frontend/lib/auditView.ts:326-359`、审计页 `:241-244`）。
- **P1**：审计页 `loadSession` 失败无 catch/重试（`frontend/app/session/[id]/audit/page.tsx:327-331`）。
- **P1**：`VERSION_CONFLICT` 用 `toast.success` 提示（`frontend/components/brief/BriefCard.tsx:130`）。

## 本轮修复

- 后端：`services/command_service.py` +99（`start_initial_run` 同事务建会话首消息 + 首个 clarify Run；`RunOutcome.follow_up` + `_enqueue_follow_up` 串 brainstorm→synthesis）、`domain/contracts.py` 增内部动作 `synthesis`、新增 `backend/workflows/synthesis.py`（主持人 1 次归并 + 至多 1 次修复 + 至多 2 位专家复核；degraded 明确降级）、`workflows/runner.py` 注册 `workflows.synthesis.run_synthesis`、`api/session_routes.py`、`api/v2/common.py`、`workflows/brainstorm.py`、`tests/test_synthesis.py`（8 例）。
- 前端：新增 `frontend/lib/phaseRouter.ts`；`store/sessionStore.ts` +79（`v2Phase`、`hydrateV2`、`sendMessage` 按阶段路由、`answerInterview` 改 async 返回 boolean、ControlReceipt 按 `related_run_id` 订阅）；`InputBox` / `InterviewBanner`（审计横幅 +「重新审计」）/ `Header`（V2_PHASE_LABELS）/ `NavButtons`（入口改 `/session/{id}/audit`）/ `ChatPanel`（「开始讨论」）/ `BriefCard`（按 `brief.status` 收敛）/ `auditView.ts` / 审计页 +144（重新审计、ContinuationNotice、closing 退避轮询）/ 旧 interview 页（结束面试改发 `end_audit`）；`tsconfig.test.json` 纳入 jsx 测试面。

## 复测

- 后端 500（+4）、前端 `npm test` 87 pass、`tsc --noEmit` exit 0、build 通过。
- 修复规模：23 文件 +794/−100；写权限边界（`backend/**` 与 `frontend/**` 分属不同 agent）未被越过。