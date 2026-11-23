# 浏览器交互验收（Browser QA）

本机 npm registry 不可达，前端装不了 jsdom / RTL / Playwright 依赖，所以 `npm test`
只能覆盖纯逻辑与服务端静态渲染（见 `frontend/tests/components-render.test.cjs`）。
为了补上「页面级真实交互」这一类证据，这里记录一套**用已连接的浏览器 MCP
（Playwright）对 3001 上的真实前端**执行的验收流程，并把结果截图归档到
`docs/loop-runs/evidence/`。

> 结论先说：本流程不是 CI 用例，而是**可复现的人工/半自动验收协议**。
> 凡是这里记录的结论，都对应一张真实截图 + 一条真实的数据库记录。

## 1. 前置条件

| 项 | 要求 |
| --- | --- |
| 后端 | `start.bat` / `scripts/launch.ps1` 拉起，API 在 `127.0.0.1:8001` |
| 前端 | 同一启动器拉起的 `next dev`，在 `127.0.0.1:3001` |
| 登录态 | 用后端签名的 Cookie（**不发邮件、不绕过安全边界**，仅本机 QA 使用） |
| 模型 | 无有效 `LLM_API_KEY` 时，所有模型 Run 会失败，这正好可以用来验证「失败是否可见」 |

为什么不能走真实登录：验证码在库里只存 `code_hash`，不可反推；因此 QA 用
`core.auth.create_session_token(email)` 铸造一个签名 Cookie（与服务端登录后下发的
Cookie 同构、同签名、同过期语义）。

```bash
# backend 目录下
PYTHONPATH=. ../.venv/Scripts/python.exe -c "
from core.auth import create_session_token
from core.config import settings
print(settings.auth_cookie_name)
print(create_session_token('qa.browser@example.com'))
"
```

拿到 `pm_brainstorm_session=<token>` 后，在浏览器里以 `127.0.0.1` 域写入该 Cookie
（必须与 API 同主机名，否则 `samesite=lax` 的 Cookie 不被跨站请求携带）。

## 2. 验收步骤与预期

按顺序执行，每一步都应「有可见结果」或「有可查的库记录」：

1. **登录态**：打开 `http://127.0.0.1:3001/`，不应跳转 `/login`，标题为
   `PM Brainstorm｜产品决策智能工作台`。
2. **创建会话**：首页填「描述你正在思考的产品问题」→ 点「开始脑暴与审计」→
   跳到 `/session/<id>`。预期：后端同事务写入 `seq=1` 的用户消息并排首个
   `clarify` Run（前端**不重复提交**）。
3. **首轮失败可见**：`clarify` Run 因模型鉴权失败 → 界面上「思考中…」消失，
   群聊里出现可操作文案：
   「模型服务鉴权失败（API Key 无效、已过期或无权限）：请更新 backend/.env 的
   LLM_API_KEY 后重启服务（start.bat / stop.bat）」。
4. **同步命令**：点「跳过引导，直接脑暴」→ 预期：phase 变 `brainstorming`、
   简报显示「已跳过」、画布版本 +1、出现「进入审计 / 进入 Loop 工作室」入口、
   输入区切到多角色脑暴与 `@谁` 选择。
5. **脑暴失败可见**：点「开始讨论」→ 预期：`brainstorm` Run 建立并以
   `LLM_AUTH_ERROR` 失败，群聊出现同一条可操作文案与「重试上一步」。
6. **Loop 工作室**：进 `/session/<id>/loop` → 预期：闭环参数（范围 / 最大轮次 /
   目标分 / 聚焦说明）可编辑，点「启动闭环」后出现「闭环已启动，第 1 轮已排队」，
   随后该轮失败并显示：阶段时间线 `0/5`、本轮评分「未生成」、本轮决策「失败」、
   **失败原因**（同一条鉴权文案）、「重试本轮」「开启新闭环」。
7. **审计页**：进 `/session/<id>/audit` → 预期：显示「开始审计」与六维说明，
   并标注审计锚定的产品状态版本（v2）。

8. **刷新后的失败可见性（CHAT-03）**：在聊天页按 F5 刷新，store 的内存态全部丢失，
   预期仍能看到「模型服务鉴权失败…」错误条与「重试上一步」按钮（原因是
   `loadSession → hydrateFailedRun` 从 `GET /sessions/{id}/runs?status=failed,partial_succeeded`
   恢复了最近一条**对话类**失败任务，闭环/审计的失败不会挤进群聊）；
   点「重试上一步」应创建一个 `parent_run_id` 非空的子 Run，并再次给出可见失败原因。

9. **决策条吸底（长页面可决策）**：在 `/session/<id>/loop` 且闭环处于
   `awaiting_decision` 时，滚动页面 → 预期「本轮决策」卡片始终贴在视口底部
   （`position: sticky` + `bottom: 8px` + `z-index: 20`），三个按钮
   「继续迭代 / 交付 / 停止」均可用（未审计中、非历史闭环）。
   验收口径（控制台执行）：
   ```js
   const s = [...document.querySelectorAll("section")].find((el) => el.textContent.includes("本轮决策"));
   getComputedStyle(s).position;                         // "sticky"
   getComputedStyle(s).bottom;                           // "8px"
   [...s.querySelectorAll("button")].map((b) => [b.textContent.trim(), b.disabled]);
   // [["继续迭代", false], ["交付", false], ["停止", false]]
   ```
   证据：`docs/loop-runs/evidence/browser-loop-sticky-decision.png`。

10. **产物损坏不炸页面（降级）**：把某条 `loop_artifacts.payload_json` 改成非法载荷
    （例如 `prototype.limitations` 写成数组），刷新闭环页 → 预期页面仍 200、
    该产物显示「未生成」，其余产物照常渲染，服务端日志出现
    `闭环产物反序列化失败，已跳过：loop_id=… artifact_id=… kind=prototype`。
    对应回归：`backend/tests/test_loop_artifact_degrade.py`。

## 3. 本次实测记录（2026-11-20）

| 步骤 | 观察结果 | 证据 |
| --- | --- | --- |
| 1 | 未跳登录，页面标题正确 | — |
| 2 | 跳到 `/session/dd5cb7e7…`；库中 `runs` 出现 `clarify` | 截图 `browser-chat-auth-error.png` |
| 3 | 出现完整可操作鉴权文案；「思考中」结束 | 同上 |
| 4 | phase `brainstorming`、brief `skipped`、`current_product_version=2` | 截图同上（简报「已跳过」） |
| 5 | `brainstorm` Run `failed / LLM_AUTH_ERROR`，出现「重试上一步」 | 同上 |
| 6 | 闭环 `start_loop` Run `failed / LLM_AUTH_ERROR`，loop `status=failed`、`loop_version=3`，页面展示失败原因与两个恢复入口 | `browser-loop-failed-round.png` |
| 7 | 审计页正常渲染，标注「基于当前产品状态（v2）」 | `browser-audit-page.png` |
| 8 | 刷新后仍显示失败原因 + 「重试上一步」；点击后新建 `parent_run_id` 子 Run 并再次可见失败 | `browser-reclaimed-failure.png`；库中 `runs` 出现 child=1 的 `brainstorm` |

库侧交叉验证（`backend/data/app.db`，同一会话）：

```
clarify            failed     LLM_AUTH_ERROR
skip_clarification succeeded  (phase → brainstorming, brief_status=skipped)
brainstorm         failed     LLM_AUTH_ERROR
start_loop         failed     LLM_AUTH_ERROR   loop_id=c7b0a19c… status=failed
messages: 3 条（seq1 人类输入 / seq2 系统提示 / seq3 brainstorm 指令）
```

## 4. 已知边界

- **未进 CI**：需要浏览器 MCP 与人工/半自动驱动；CI 里只有 `npm test` +
  `npm run test:e2e`（后端 API smoke）。
- **登录态由 Cookie 铸造**：仅适合本机 QA；生产验收应走真实验证码或真实 OIDC。
- **模型失败是当前环境的常态**（`LLM_API_KEY` 401），所以本流程验证的是
  「失败可见 + 状态一致 + 恢复入口」，而不是成功路径的产物内容。换有效 Key 后，
  同一流程可以直接观察到五阶段产物与评分卡。
- **截图体积**：单张 400–600KB，只作为归档证据，不参与构建。

## 5. 复跑

1. 确认 `8001 /health` 与 `3001` 首页均 200；
2. 重新铸 Cookie（上面命令）并在浏览器写入；
3. 按 §2 逐步执行，每步对照 §3 的观察点；
4. 如需留证，把截图写入 `docs/loop-runs/evidence/`，并在本文件 §3 表里追加一行。

## 6. 自动化 CDP 验收（`scripts/browser_e2e/`，第 13 批新增）

§2 的人工协议保留作探索性验收；三个关键 AC 现在有可复跑的自动化脚本（零新依赖，
本机 Chrome + Python `websockets`，不装 Playwright）：

```bash
cd backend
../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py            # 全部
../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py AC-19      # 单个场景
```

| 场景 | 断言要点 | 截图 |
| --- | --- | --- |
| AC-01 | 打开种子会话后 `reload()` ×3：原始用户消息页面恰好 1 条；`messages=2`、`runs=1`、`session_version`/`product_version` 不变；无未捕获异常 | `docs/browser-qa/e2e/ac-01-refresh-x3.png` |
| AC-19 | 8 个分支（技术/设计/商业/用户/方案/决策/未决/实验）全部渲染；无「+N 条更多」；第 8 分支节点原文、`v2` 版本徽标、引用计数可见 | `docs/browser-qa/e2e/ac-19-eight-branches.png` |
| AC-31 | 点真实「退出登录」→ 换 B Cookie 登录：历史抽屉只有 B、页面无 A 消息、storage 无 A 会话 ID、直接打开 A 会话显示「加载失败」 | `docs/browser-qa/e2e/ac-31-account-a.png`、`ac-31-account-b.png` |

实现说明：

- `cdp.py`：极简 CDP 客户端（`Target.createTarget` / `Page.navigate` / `Runtime.evaluate` /
  `Page.captureScreenshot` / `Network.setCookie`），负责启动/关闭 headless Chrome；
- `seed.py`：走**真实仓储**（`session_repo` / `run_repo` / `audit_repo`）直接播种会话、产品状态与
  `active` 审计，只跳过 LLM 调用，因此前端读到的仍是真实 API 投影；跑完自动硬删种子会话；
- 本机 venv 的 Python 3.12 会把子进程句柄继承列表收窄，Chrome 的沙箱子进程会以
  `STATUS_BREAKPOINT(0x80000003)` 立刻退出，因此 headless 启动追加 `--no-sandbox`
  （只影响验收浏览器，不影响被测应用的安全模型）；
- 该脚本不进 CI（CI 无图形栈/Chrome），属于发布前本地必跑项，见
  `docs/v4-release-checklist.md` §1。
