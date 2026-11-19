# Round 2 — 64 / 100

- 时间：2026-11-19 10:59（评分）
- 结论：**不收敛**。后端 505 OK、前端 tsc 0 错 / 101 pass（evaluator 自跑），但会话生命周期仍有 2 条 P1 + 4 条 P2 状态缺陷。
- 无 P0；P1 计 2 条，未触发 85 分上限。

| 维度 | 得分 | 满分 |
| --- | --- | --- |
| 功能正确性 | 18 | 25 |
| 数据一致性与状态机 | 15 | 20 |
| 错误处理与边界 | 9 | 15 |
| 前端逻辑与交互 | 6 | 15 |
| 架构与代码质量 | 7 | 10 |
| 自动化测试 | 6 | 10 |
| 文档与可运维 | 3 | 5 |

## 发现（共 20 条，5 条 P0/P1）

- **C-01（P1）跨会话画布/版本串用**：`loadSession` 的 `set` 不含 `canvas/productVersion/productState/sessionView`（`sessionStore.ts:1296-1313`），`hydrateProductAndCanvas` 用全局版本做门限（`:483-499`），审计页用全局 `productVersion` 算期望版本（审计页 `:422`、`:437`）→ A(v5) → B(v1) 后 B 的审计必被后端 `VERSION_CONFLICT` 拒绝（`command_service.py:942-947`）；且 `canvases` / `productStates` 只写不读，按会话缓存形同虚设。
- **C-02（P1，复核后升级）无限重连**：`reconnectRun` 的 `attempt` 恒为 0（唯一调用点 `sessionStore.ts:897` 传字面量 0，`:860` 的 `attempt >= 2` 永不成立），EOF/非 2xx 后以固定 600ms 无限 `getRun` + 重开流，`isStreaming` 永久 true，用户无停止入口。
- **C-03** 刷新后横幅重现，可重复创建第二个 brainstorm Run（`ChatPanel.tsx:33-37` 依赖内存 `runs`，`hydrateV2` 不重建）。
- **C-04** `mergeMessages` 无条件剥掉所有 `local-` 乐观消息（`sessionStore.ts:360-370`），用户刚发的消息会在首 token 后消失直到 Run 结束。
- **C-05** `productVersion` 一变就清空简报草稿（`BriefCard.tsx:67-71` 依赖 `[productVersion]` 而非 `brief.id/version`）。
- **C-06** 审计读取失败被当成「没有审计」（`sessionStore.ts:531-540` 空 catch），可能再起一个审计把进行中的弃置。
- 后端缺口：AC-14 仍是整 Run 重放（`command_service.py:1788-1860` 只记 `parent_run_id`）；AC-05 只做长度/形状校验（`contracts.py:513,539`），evidence 归属未回查。

## 本轮修复

- 会话级版本基线：`canvases` / `productStates` 按 `sessionId` 读取并参与版本门限；`loadSession` 重置会话级 v2 状态。
- `reconnectRun` 真退避（attempt 递增、上限 2 次、失败提示刷新）+ 停止入口。
- 审计错误态与「从未审计」区分（`resolveAuditEntryState`）、审计页加载失败可重试。
- 乐观消息合并改为「确认后按 local_id 替换」而非全量剥离；`briefDraft.ts` 保留草稿（按 `brief.id`）。
- 刷新后不重复开 brainstorm（`hydrateV2` 重建 `runs` 视图）。

## 复测

- 后端 505 OK；前端 `npm test` 101 → 115 pass、tsc 0、build 7 路由。
- 修复规模：累计 25 文件 +1110/−123。