# Round 5 — 72 / 100（终轮）

- 时间：2026-11-19 13:46（评分）
- 结论：**不收敛**（72 < 95）。核心后端与状态机扎实（evaluator 实跑 508 用例、出现 1 个环境相关错误），但 3 条前端 P1 + 1 条新发现的后端 P1（legacy JSON 存储原子写竞态）+ 文档 3 处事实错误。
- 交叉证据：后端 `Ran 508 tests`；`npx tsc --noEmit` exit 0；`npm test` 123/123；`npm run build` 7 路由；`npm run test:e2e` 1 pass。

| 维度 | 得分 | 满分 |
| --- | --- | --- |
| 功能正确性 | 20 | 25 |
| 数据一致性与状态机 | 16 | 20 |
| 错误处理与边界 | 11 | 15 |
| 前端逻辑与交互 | 8 | 15 |
| 架构与代码质量 | 8 | 10 |
| 自动化测试 | 6 | 10 |
| 文档与可运维 | 3 | 5 |

## 发现

- **P1（后端）legacy JSON 存储原子写竞态**：`backend/db/session_store.py:59` 的 `os.replace` 在 Windows 负载下约 40% 概率抛 `PermissionError [WinError 5]` 并静默丢消息（`backend/tests/test_session_store.py:51` 断言 40 条，实测 39 条）。
- **P1（前端）故障分类过宽**：`frontend/lib/api.ts:134-140` 把 404/405/410/501/502/503 全判为「端点缺失」→ `store/sessionStore.ts:1504-1515` 永久切 legacyMode，启动期一次 503 即让整会话静默降级。
- **P1（前端）版本 0 污染**：`store/sessionStore.ts:517-518` 用 `??` 取版本，0 会拼成 `product_version=0` → 后端 400，画布重试永久失败。
- **P1（前端）SSE 无超时**：`frontend/lib/sse.ts` 无空闲超时，半开连接无出口。
- **文档失真**：`docs/v4-acceptance-report.md:74` 把 AC-16 标「未实现」（实际 `backend/workflows/synthesis.py` + `tests/test_synthesis.py` 8 例已实现并注册）；AC-22 声称覆盖 `GET /audits` 但 `tests/` 内零处请求；用例计数过期（496/492/87 → 实测 508/123）。
- **功能缺口**：AC-14 仍是整 Run 重放；AC-19/AC-20 无浏览器渲染证据；AC-09 无真崩溃注入；AC-23 无两进程同时回答审计问题；AC-30/AC-42 的取消竞争仍是顺序用例；`backend/` 下遗留 125 个 `runner-test-*` / `tmp*` 目录。

## 本轮修复

R5 未再进入修复轮（5 轮上限）。以下三项在 Loop 收口后由 Lead 手工修复并补测（见 [README](README.md) §3）：

1. `backend/db/session_store.py`：唯一临时文件 + 有界重试 + 失败清理/抛错；新增 2 条用例（重试成功、重试耗尽）+ 并发用例连跑 10 次验证。
2. `frontend/lib/api.ts`：`isEndpointUnavailable` 收窄为 404/405/501 + 网络错误；新增 3 条断言。
3. `frontend/store/sessionStore.ts`：`pickVersion()` 只接受 `> 0`。

文档失真同在本次收口修正（AC-16 状态、AC-22 证据、用例计数）。

## 终态

- 后端 **510 用例 OK**；前端 **tsc 0 错 / 123 pass / build 7 路由 / e2e 1 pass**。
- 未闭合项见 [README](README.md) §5。