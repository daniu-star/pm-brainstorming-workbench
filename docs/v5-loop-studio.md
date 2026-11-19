# Loop Studio（v5）：把「开产品」的闭环装进产品脑暴平台

> 状态：**已交付并实测**（2026-11-19）。分支 `feat/prd-v4-m1`。
> 接口与文件归属：见 [`docs/loop-studio-interfaces.md`](./loop-studio-interfaces.md)。
> 主验收记录（V4 平台侧）：[`docs/v4-acceptance-report.md`](./v4-acceptance-report.md)。

## 1. 它解决什么问题

V4 之前，平台只能「设想」：澄清 → 四角色共创 → 审计。用户拿到一堆对话和结论，
却要离开平台才能把它变成可检验的东西。Loop Studio 把**闭环工程（Loop Engineering）**
搬进产品，让一次会话直接产出：

1. **设想**：可检验的 MVP 规格（功能、验收标准、假设、不做什么）
2. **检验**：逐功能的验证问题与结论（pass / unknown / fail）
3. **开发**：可运行的 MVP 原型（文件清单 + 入口 + 启动命令）
4. **测试**：逐功能的测试用例与执行报告
5. **评测**：七维评分卡 + 结论 + 阻塞项 + 下一步

一「轮」= 五阶段一次完整走完。评分达到目标分后闭环停在 `awaiting_decision`，
必须由**用户**选择 `iterate` / `ship` / `stop` —— 模型不会自己宣布成功。

## 2. 用户怎么用

```
会话（brainstorming / audit_completed）
  └─「进入 Loop 工作室」→ /session/{id}/loop
        ├─ 启动闭环：scope(mvp|feature|improvement) / max_rounds(1..5) / target_score(60..100) / focus
        ├─ 看得到：五阶段时间线（pending/running/done/failed/skipped）
        │          七维评分条 + 总分 + verdict + 阻塞项
        │          五个产物 Tab（规格 / 检验 / 原型 / 测试 / 评分卡）
        └─ 决策条：继续迭代（耗尽轮次时禁用并说明原因）/ 交付 / 停止
```

命令行等价物（`POST /api/v2/sessions/{sid}/commands`）：

```json
{"action": "start_loop", "payload": {"scope": "mvp", "max_rounds": 3, "target_score": 85, "focus": "多人协同"}}
{"action": "loop_decision", "payload": {"loop_id": "<id>", "expected_loop_version": 2, "decision": "iterate", "note": "补邮件通道"}}
```

## 3. 架构落点

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 契约 | `backend/domain/contracts.py` | 13 组 Loop 枚举、16 个模型、`start_loop`/`loop_decision`/内部 `loop_round`、事件 `loop.updated`、`RunReceipt.loop_id`、`RunSnapshot.loop`、`SessionView.current_loop_id` |
| 状态机 | `backend/domain/state_machine.py` | 三个动作仅 `brainstorming / audit_completed` |
| 存储 | `backend/repositories/database.py`、`loop_repo.py` | 4 张表（`loops` / `loop_rounds` / `loop_stage_results` / `loop_artifacts`）+ `runs.loop_id`；仓库只 flush，不 commit |
| 命令 | `backend/services/command_service.py` | `start_loop` 建闭环 + 第 1 轮 Run；`loop_decision` 排下一轮或同步收口（`ship`/`stop`） |
| 编排 | `backend/workflows/loop.py` | `run_loop_round`：五阶段步骤、阶段产物 staging、最多一次修复、`awaiting_decision` 不自动续轮 |
| 提示词 | `backend/core/loop_prompts.py` | `PROMPT_VERSION="v5.0"`、五个 PURPOSE、阶段间只传摘要 |
| 确定性校验 | `backend/services/loop_harness.py` | 7 个纯函数：规格/检验/原型/测试报告校验、评分硬上限、结论推导、功能覆盖 |
| API | `backend/api/v2/loops.py` | 4 个只读端点（列表/快照/轮次/产物） |
| 前端 | `frontend/app/session/[id]/loop/page.tsx`、`lib/loopView.ts`、`store/sessionStore.ts` | 工作室页面、纯函数视图层、loop 缓存与事件合并 |

### 3.1 一轮的执行形状

```
Run(action=start_loop|loop_decision, task_kind=loop_round)
 ├─ step logical_key="loop.round"                （runner 顶层）
 ├─ step "loop.stage.ideate"    → LoopMvpSpec
 ├─ step "loop.stage.validate"  → LoopValidation
 ├─ step "loop.stage.develop"   → PrototypeSpec
 ├─ step "loop.stage.test"      → LoopTestReport
 └─ step "loop.stage.evaluate"  → LoopScoreCard
       ↓ loop_harness 覆盖模型自评分（硬上限）
   RunOutcome(事件: loop.updated × 5 + product_state.updated × 1 + 恰好一个终结事件)
```

- 阶段之间只传**摘要**，原始 JSON 不回流；
- staging（step 行 / 阶段结果 / 产物）走独立短事务，崩溃可用同一 Run 重放，已完成阶段不重复调模型；
- 每轮上游调用上限 10（启动预检 5，每阶段至多一次修复）；正常路径恰好 5 次；
- 产品状态每轮 +1 版本：must-have 功能转 claims、阻塞项转 unresolved issues、推荐方案转 proposed decision；
- 闭环「完成判定」只由阈值 + 用户决策决定，不由模型自述。

### 3.2 评分与结论规则（确定性，不靠模型）

七维 100 分：功能可用性 25 / 技术可行性 15 / 用户价值 20 / 风险与验证 15 /
交付完整度 10 / 可测试性 10 / 文档与交接 5。

`clamp_score` 硬上限（叠加确定性阻塞项后压总分）：

| 条件 | 总分上限 |
| --- | --- |
| 任意 P0 阻塞（如 must 功能未覆盖、原型缺入口、模拟测试未声明） | 70 |
| ≥3 条 P1 阻塞 | 85 |

`derive_verdict`：达目标分且无 blocker → `pass`；轮次未耗尽 → `iterate`；≥3 blocker → `blocked`。

## 4. 实测证据（2026-11-19，本机）

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests` | **Ran 577 tests / OK**（71.5s） |
| Loop 后端专项 | `-m unittest tests.test_loop_harness tests.test_loop_workflow tests.test_loop_runner tests.test_loop_commands tests.test_api_loops` | 39 + 13 + 13 = **65 OK** |
| 五阶段真机闭环（可复跑脚本） | `cd backend && ../.venv/Scripts/python.exe ../scripts/loop_e2e/run_loop_e2e.py`（真 `RunWorker` 子进程 + 假 LLM + 独立 SQLite） | **29/29 PASS / LOOP_E2E_OK** |
| 前端类型 | `cd frontend && npx tsc --noEmit` | exit 0 |
| 前端单测 | `npm test` | **150 / 150 pass** |
| 前端构建 | `npm run build` | exit 0，含 `ƒ /session/[id]/loop 9.14 kB` |
| 前端 e2e 冒烟 | `npm run test:e2e` | 1 pass（真实拉起 uvicorn，v2 401 语义） |
| 运行态 | `start.bat` 一键栈 | `/health` ok；`/`、`/session/x/loop` 均 200 |

E2E 断言覆盖：五阶段 step 全 succeeded、五类产物齐全、评分卡 total=88 且 verdict=pass、
`loop.updated`×5 与 `product_state.updated`×1、恰好一个终结事件、闭环进 `awaiting_decision`、
`iterate` 起第 2 轮、`ship` 收口（loop=shipped、决策备注落库、`active_run_id` 释放）。

## 5. 已知边界与偏差（如实记录）

1. **测试执行是「模拟执行」**：M1 的 test 阶段由模型给用例与结果，产物必须标
   `simulated=true` 且在 notes 注明「未实际执行」；harness 对未标注者记 P0 阻塞、总分压到 70。
   真实跑测试（沙箱执行）不在本期范围。
2. **原型「需人工落地」**：产物是文件内容 + 启动命令，不在服务端执行构建；`requires_human_build` 显式提示。
3. **每 Run `step.started` 为 6 条**（runner 顶层 `loop.round` + 5 个阶段），契约文档措辞已按此更正。
4. **`UnresolvedIssue.kind` 用 `gap`**（枚举无 `blocker`），不新增枚举以免破坏既有读端。
5. **`clamp_score` 只压总分**，不重分配维度分；维度分之和可能大于 total（前端展示以 total 为准）。
6. **前端不懒加载产物**：快照内嵌五类产物（体积小、少一次往返）；`getLoopArtifact` 已封装备用。
7. **Loop 入口只在 `brainstorming` / `audit_completed`**（与状态机一致）；审计进行中不显示。
8. **无浏览器级点击验证**：本机 registry 不可达、无 Playwright 依赖，前端以 SSR 冒烟 + 纯函数单测覆盖。
9. **`next build` 与 `next dev` 共用 `.next`**：开发服务器运行时执行构建会破坏其路由缓存
   （表现为动态路由全 500）。发布流程里应先 `stop.bat` 再 build，或构建后再 `start.bat`（已写入发布清单）。

## 6. 下一步（如果要继续）

- 真实测试执行器：把 test 阶段的用例真正跑在沙箱里，产物落真实日志与截图；
- 产物懒加载与版本对比（两轮评分卡/规格 diff）；
- 闭环模板：把一次闭环沉淀为可复用模板（同 scope/focus 直接起第二轮产品）；
- 与审计打通：审计的 `recommended_experiments` 一键转成下一轮 loop 的 `focus`。
