# 多角色 Lo op Engineering 运行记录（2026-11-19，产品打磨型）

> 本次运行使用 `skills/loop-engineering-product/` 里的闭环工作流（7 角色 / 8 维评分 / 硬上限规则 /
> 写权限隔离），目标是把产品从「能跑通」推到「可被顶尖研发团队验收」。**未达 95 分收敛目标**，
> 停在第 3 轮，如实记录分数、发现与修复。

## 1. 运行元数据

| 项 | 值 |
| --- | --- |
| Workflow job | `workflow-95e7801be8c14080`（`loop-engineering-product-continue`） |
| 子代理数 | 31 |
| 轮次上限 / 目标分 | 3 / 95 |
| 最终分数 | **66/100（第 3 轮）**，`reachedTarget=false` |
| 逐轮分数 | R1 **72** → R2 **73** → R3 **66** |
| 写域隔离 | 客户端只写 `frontend/**`，服务端只写 `backend/**`，测试组只写 `**/tests/**` |
| 证据基线 | 每轮评测组必须先真跑一条命令；禁止删断言/skip/改期望值变绿 |

## 2. 逐轮结果

### 第 1 轮：72/100

| 维度 | 得分 |
| --- | --- |
| 产品与需求达成 | 15/20 |
| 功能正确性 | 11/15 |
| 数据一致性与状态机 | 11/15 |
| 交互与前端逻辑 | 9/15 |
| 架构与代码质量 | 7/10 |
| 安全与错误处理 | 7/10 |
| 自动化测试与证据 | 8/10 |
| 文档与可运维 | 4/5 |

主要 P1：
- 画布决策节点没有任何「确认决策」入口（`confirm_decision` 后端完整、前端零接线）。
- `BriefCard` 不按 phase 门控；`revise_brief` 命中 `audit_completed` 时静默把阶段退回 brainstorming。
- 工作台 → 审计页的客户端跳转丢失运行中 Run 的订阅，审计页不再自动推进。

本轮后：后端 595 用例 OK、前端 175 用例 pass。

### 第 2 轮：73/100

主要 P1（去重后 3 类）：
- **闭环轮次 Run 排队超时后永不收口**：`expire_queued_runs` 把 Run 置 `failed(QUEUE_WAIT_EXCEEDED)`，
  但 `loop.status` 仍是 `running` → `start_loop` 409 LOOP_ACTIVE、`loop_decision` 409
  LOOP_NOT_AWAITING_DECISION，闭环永久卡死；retry 子 Run 还丢了 `loop_id`。
- 发送失败后重试成功仍报「消息发送未确认」，再点重试会真的重复发送（乐观消息未回滚）。
- v2 响应 200 但非 JSON 时静默降级 legacy，审计/闭环/简报入口整体消失且无提示。

本轮后：后端 603 用例 OK、前端 185 用例 pass。

### 第 3 轮：66/100（发现 P0）

**P0：每轮闭环用整体替换写回产品状态** —— `backend/workflows/loop.py` 以本轮新列表覆盖
`claims/options/decisions/unresolved_issues`，上一轮（含脑暴期）的产物与用户已确认决策在第 2 轮消失，
并留下悬空的 `experiment → claim` 引用。违反 `docs/loop-studio-interfaces.md` 的「追加」契约。

主要 P1：
- 已确认简报再修订必填字段会退回「待确认」，但脑暴/审计阶段没有确认入口。
- 审计开场/报告失败后页面只显示「已失败」，无失败原因与恢复入口。
- 闭环交付（ship）后产品状态里的决策仍是 `proposed`，画布继续显示「确认决策」。

本轮修复（已落盘）：
- `loop.py` 引入 `_append_new` 合并写回（保留既有条目 + 保持已确认决策状态），并补两轮回归用例。
- ship 时把本闭环决策置 `confirmed` 并 bump 产品版本。
- 审计页保留原始 `end_reason`、渲染失败原因、提供「重试生成报告」；结束审计加二次确认。
- 前端：事件流 4xx 不再当作可重连断线；重试复用 `request_id`；413/409 自动同步版本。

本轮后：后端 610 用例 OK、前端 194 用例 pass。

## 3. 失败/未收敛原因（如实记录）

1. **收敛目标定得比修复速度高**：5 轮上限下每轮仍有 3~4 条 P1 被打出，R3 又出现 1 条 P0，
   分数在 66~73 区间震荡，没有进入「只做最高 ROI 一件事」的收敛区间。
2. **验证深度受限**：本机 npm registry 不可达（无法装 Playwright/RTL/jsdom）、8000/3000 端口被其它项目占用，
   页面级/浏览器级证据只能用 `tsc + node --test + SSR 冒烟`，交互缺陷主要靠静态审计发现。
3. **产品面尚未补齐**：简报重确认、画布决策确认、闭环历史回看、失败恢复引导这类「产品功能缺口」
   不是一轮修复能闭合的，需要产品决策而不是代码补丁。

## 4. Loop 之后的热修（本次一并交付）

- **`steps.created_at` 缺列导致「发消息一律失败」**（`INTERNAL / no such column: steps.created_at`）：
  在 `repositories/database.py::_additive_schema_upgrade()` 补列 + 回填，新增
  `backend/tests/test_schema_upgrade.py`（4 用例）。
- **模型 401 静默变成「任务失败」**：新增 `LLMAuthError`（`LLM_AUTH_ERROR`），错误信息直接提示更新
  `backend/.env` 的 `LLM_API_KEY` 并重启；新增 `scripts/check_llm.py` 一键自检（区分 key 无效/余额不足/
  模型名错误/限流/网络不可达），README 补排查说明。
- 一键启动器支持「按服务独立重启」，`/health/detail` 暴露 worker 心跳与队列计数。

## 5. 如何复跑

```bash
# 单轮闭环（改 target / maxRounds 即可）
node -e "…"   # 或直接在工作流工具里执行 skills/loop-engineering-product/scripts/loop-workflow.js
```

- 工作流脚本：`skills/loop-engineering-product/scripts/loop-workflow.js`
- 写域校验：`python skills/loop-engineering-product/scripts/verify_write_scopes.py --baseline <sha>`
- 评分标准与硬规则：`skills/loop-engineering-product/references/{rubric,harness}.md`

## 6. 复跑后的当前基线（真跑）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests` | **Ran 615 tests / OK**（72.5s） |
| 前端类型 | `cd frontend && npx tsc --noEmit` | exit 0，无诊断 |
| 前端单测 | `cd frontend && npm test` | **194/194 pass**，0 skip |
| 一键启动 | `start.bat` → API 8001 / Worker / Web 3001 | 三个服务均 ready |
