# QA Loop 执行归档（PRD V4.0 项目）

> 闭环定义与评分卡见 [../qa-loop-engineering.md](../qa-loop-engineering.md)。
> 本次执行：2026-11-19 09:02 → 13:46（Asia/Shanghai），workflow `prd-v4-qa-loop`，37 个子 agent。

## 1. 结论

**未收敛**：跑满 5 轮，最终 **72 / 100**（阈值 95）。reached95 = false，stopRound = 5。

| 轮次 | 总分 | 主要问题 | 每轮修复规模 |
| --- | --- | --- | --- |
| R1 | 62 | 审计完成后主循环断裂（P0×1）+ 4 条 P1，AC-16 synthesis 缺失 | 23 文件 +794/−100 |
| R2 | 64 | 跨会话画布/版本串用、无限重连等 5 条 P0/P1 | 2 文件→累计 25 文件 +1110/−123 |
| R3 | 71 | 无 P0；UX-01 首屏二次提交、UX-02 审计收尾失败不可恢复 | 累计 28 文件 +1941/−183 |
| R4 | 67 | 复核发现 R3 两项修复未闭环 + 旧 JSON 存储原子写竞态、文档失真 | 累计 31 文件 +2303/−207 |
| R5 | 72 | 3 条前端 P1 + 1 条后端 P1（legacy store `os.replace` 竞态）+ 文档 3 处事实错误 | 同上（预算内不再新增修复） |

分数变化不是单调上升：每轮由独立 evaluator 重新取证，R4/R5 才暴露旧存储竞态与文档失真（R3 的 71 分未覆盖这些面），所以出现 71 → 67 → 72 的波动。

**数据来源**：R1/R2/R3/R5 的分数与维度来自 evaluator 的结构化 `SCORE` payload（可从子 agent 会话日志恢复，见 §4）；**R4 的 payload 未落盘**，只保留了 verdict 片段（"不通过（67/100…）"），因此 R4 只有总分与结论、无维度明细——此处如实标注，不做补猜。

## 2. 逐轮修复主线

1. **R1**：新增 `start_initial_run`（会话 + 首条 user 消息 + 首个 clarify Run 同事务，占住 `active_run_id`）、`RunOutcome.follow_up` + `_enqueue_follow_up`（brainstorm 成功后自动排 synthesis）、内部动作 `Action.synthesis`、`backend/workflows/synthesis.py`（主持人归并 + 至多 1 次修复 + 至多 2 位专家复核）、`frontend/lib/phaseRouter.ts` + `store.v2Phase`（auditing / audit_completed 不再被旧 interview 吞掉）、审计入口与「重新审计」。
2. **R2**：会话级版本基线（`canvases` / `productStates` 真正按会话读）、`reconnectRun` 退避与上限、刷新后不重复开 brainstorm、乐观消息合并、`briefDraft` 保留草稿、审计读取失败与「从未审计」区分。
3. **R3**：首屏不再重发 `?problem`（只 `loadSession`）、审计 closing 失败写入 `failed` 并提供续跑入口、`end_audit` 后取消优先。
4. **R4**：预算账本跨 epoch 恢复、软删与越权补测、错误码文案（`INVALID_REQUEST` → 引导先确认产品定义）。
5. **R5**：文档计数与 AC 状态校正（本次收口一并完成）、`pending_continuation` 前端消费。

## 3. 收口后补的三项 P1（Loop 之外、我手工修复）

| 缺陷 | 修法 | 验证 |
| --- | --- | --- |
| `backend/db/session_store.py` 原子写竞态（Windows 下 `os.replace` 抛 `[WinError 5]`，40% 概率丢消息） | 改 `tempfile.mkstemp` 唯一临时文件 + 有界重试（10 次、20–200ms 退避）+ 失败清理与抛错 | `tests.test_session_store` 9 OK；并发用例连跑 10 次 0 失败；新增 2 条重试/耗尽用例 |
| `frontend/lib/api.ts` 把 410/502/503 也判为「端点不存在」→ 一次临时故障永久降级 legacy | 只认 404/405/501 + 网络错误；410 走快照恢复，503/502 作为可重试错误提示 | `frontend/tests/api.test.cjs` 新增 3 条断言；`npm test` 123 pass |
| `frontend/store/sessionStore.ts` 用 `??` 取版本，0 会拼成 `product_version=0` → 后端 400、画布重试永久失败 | `pickVersion()` 只接受 `> 0` 的有限数字，否则回退会话版本 | `npx tsc --noEmit` exit 0；`npm test` 123 pass |

## 4. 证据与复现

- 后端全量（收口后）：`cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests` → **Ran 510 tests / OK**。
- 前端：`npx tsc --noEmit` exit 0；`npm test` → **pass 123 / fail 0**；`npm run build` 7 条路由；`npm run test:e2e` 1 pass。
- 逐轮 payload 恢复脚本：`.tmp/build_rounds_summary.py` → `.tmp/qa_rounds.json`（4 个 `SCORE` payload）+ `.tmp/rounds_summary.md`；读取子 agent 日志需用 `zstandard.stream_reader()`（会话文件是多帧 zstd）。
- 每轮明细：见 [round-1.md](round-1.md) … [round-5.md](round-5.md)。

## 5. 仍未闭合（下一轮靶子）

1. **AC-14**：`retry_run` 仍是整 Run 重放（成功角色会重新调用模型），未实现"只重跑失败步骤"。
2. **AC-40**：审计完成后人工更正评估、再从结构记录重生成报告，后端仍零实现。
3. **AC-09 / AC-23 / AC-42**：真崩溃注入与两进程审计竞争未自动化（现为顺序用例）。
4. **AC-19 / AC-20 / AC-33**：浏览器级渲染证据、编造数字标注集、摄像头/STT 降级——受限于本机无 playwright/jsdom 且 npm registry 不可达。
5. **AC-05**：`evidence_id` 归属仍只做格式/长度校验，未回查所属会话。
6. 事件压缩与保留（7 天 / 活动 Run 不压缩）未实现；软删只置 `deleted_at`，正文保留。