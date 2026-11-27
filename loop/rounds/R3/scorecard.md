# R3 评分卡（Loop 第 3 轮 / 第 44 批）— 独立评测组

- 评分人：`score-r3`（独立评测组子代理，**不是**修复作者；本轮只读代码 + 只读命令 + 只读网络探测，未改动任何产品代码、未 commit/push、未登录站点、未发验证码、未调用真实大模型；唯一写入是 `loop/rounds/R3/` 下的本卡与 `mkdir`）
- 评分对象：`C:\Users\13067\Desktop\pm-brainstorming-workbench`
  - **被评提交（scored target）= `eeee88faeb8fe965f6c4d2735ab0df998c9436cd`（`eeee88f`）**，即 `origin/main` 所指提交，也是 GitHub CI run `45932843781` 跑的提交。
  - 线上后端：Hugging Face Space `123chenjunliang/pm-brainstorm-workbench`，**`sha=f4fbdcf45953dec7590cac006d5691965054ffde`，`stage=RUNNING`，`lastModified=2026-11-27T03:15:09Z`**；线上前端：Vercel（`https://www.brainstorming.top`，同源 `/backend` 反代到该 Space）。
  - **部署内容一致性（强证据）**：从 Space `raw/main` 拉下的 `backend/main.py` 与 `backend/core/log_context.py`，sha256 与本仓库 `eeee88f` 工作树**逐字节相同**（`main.py` = `0512c23c…6094`，`log_context.py` = `6d5f00ec…2b91`）→ 线上跑的就是被评代码。
- 基线漂移说明：评测过程中本地分支 `feat/prd-v4-m1` 从 `eeee88f` 前进到 `bcfea41`（**docs-only**，`docs/loop-runs/README.md` +1 行、`docs/v4-release-checklist.md` +3 行，是作者在记录本批线上证据；**未推送到 `origin/main`**）。本卡只对被评提交 `eeee88f` 计分，不把该 docs 提交的任何措辞算作已交付物；结论不受影响。
- 评分基准：`loop/rubric.md`（227 行，V1.0，冻结未改；与 skill `references/03-rubric.md` 一致）。权重 A 20 / B 15 / C 15 / D 15 / E 15 / F 10 / G 5 / H 5；总分 = Σ(分×权重)/100（四舍五入到整数，**闸门用未取整值**）。
- 时间窗：2026-11-27 11:17–11:40 +08（线上只读探测 11:20–11:31 +08；本地后端两跑、前端套件、CI REST、Space API 均在本轮重跑）。
- 工作区状态：开测 `git status --porcelain` 仅 `?? CON`（非本轮产物、未触碰），收尾时工作树干净、仅新增 `?? loop/rounds/R3/`（本卡）。所有实测均在 `eeee88f` 工作树上跑；`bcfea41` 出现后未重跑计分项（其 diff 证明不含产品代码）。

---

## 1. 一页结论：**FAIL**

| 项 | 值 |
| --- | --- |
| **总分** | **88.50 / 100（四舍五入 89）**（R2 = 88.30 → **+0.20**） |
| **结论** | **FAIL** |
| 未关闭 P0 | **1 个**（P0-1 线上无持久卷 → 重启/休眠回收丢全部 SQLite 业务数据；本轮仍只有披露 + warn 级自检，无持久化落盘） |
| 封顶规则 | 命中 P0 → 总分封顶 89；本卡 88.50 < 89，封顶不额外生效（若后续加权分 >89 仍会被压到 89） |

| 维 | 分 | 权重 | 加权 | 锚点落点（一句话） |
| --- | --- | --- | --- | --- |
| **A 功能正确性与回归稳定性** | **95** | 20 | 19.00 | 后端两跑 `Ran 878 tests / OK` 一致（0 skip）、前端 334/334 + `tsc` 0 错、CI 在 HEAD 绿；A 100 仍缺「关键链路认证流 API 冒烟 100%」独立复跑 |
| **B 服务端工程质量** | **70** | 15 | 10.50 | **5 个 >1500 行文件一个没拆**（2619 / 2044 / 1885 / 1811 / 2459），命中 B 70 硬条件；本轮新增模块本身不复杂 |
| **C 前端与交互质量** | **86** | 15 | 12.90 | 本轮**前端产品代码零改动**（diff 仅 docs/PNG/backend/scripts），沿用 R2 证据：无未转义拼接、三态/aria 大体完整、冷首屏 >2s |
| **D 安全与合规** | **90** | 15 | 13.50 | 应用侧 CORS 通配拒绝仍在；**平台层 ACAC 本轮不再复现**（P1→P2）；「外部文本包裹为不可信内容」仍缺 → 维持 90 |
| **E 产品逻辑与体验** | **90** | 15 | 13.50 | 本轮产品逻辑零改动，沿用 R2：7 步闭环 + 断点兜底有真模型旅程记录；「每步下一步入口」仍未逐页实测 |
| **F 测试与可验证性** | **100** | 10 | 10.00 | CI run `45932843781`（head `eeee88f`）三 job **40/40 step 全 success**；套件 78s/24s、三 job 并行、新增 44 例；测试 mock 网络、无外网依赖 |
| **G 文档与交付一致性** | **85** | 5 | 4.25 | **实测 878 但 8 处文档写 874**（同一数字错误覆盖 4 文件）、2 处旧自相矛盾仍在、1 处「前端提示会带出 request_id」与代码不符、1 处体检 8/9 步矛盾 |
| **H 可运维性** | **97** | 5 | 4.85 | **R2→R3 +5**：日志行带 `[rid=…]` 已落地并线上部署、health live/ready + blocking_reasons + warnings、部署自检接入体检第 2 步；扣 3 分因新增自检脚本**默认文本输出在 Windows GBK 下崩溃**（README 文档命令不可直接使用） |
| **合计** | — | 100 | **88.50** | **FAIL** |

### 硬闸门逐条（rubric §4）

| 闸门 | 要求 | 实测 | 结论 |
| --- | --- | --- | --- |
| 1 总分 | ≥95（未取整） | **88.50** | ❌ 差 **6.50** |
| 2 A | ≥95 | **95** | ✅ 满足（贴线） |
| 3 D | ≥90 | **90** | ✅ 满足（贴线；平台层 ACAC 已消失，见 §3-D） |
| 4 未关闭 P0 | 0 个 | **1 个**（P0-1） | ❌ 不满足（判据见 §5） |
| 5 独立复核签字 | 非修复作者 | `score-r3` 独立评测组 | ⚠️ 本轮**不签字**（1/4 闸门未过；签字只在四项全过时给出，rubric 要求评测组签字方可 PASS） |

**结论：FAIL（2 条闸门不满足：总分、P0 未清零）。**

---

## 2. R2 → R3 对比表

| 维度 | R1（866aaf3） | R2（61454c9） | **R3（eeee88f）** | Δ(R2→R3) | 本轮依据 |
| --- | ---: | ---: | ---: | ---: | --- |
| A 功能正确性 | 95 | 95 | **95** | 0 | 878×2 全绿一致、334 全绿、CI 在 HEAD 绿；A 100 的「关键链路冒烟 100%」仍靠既有证据 |
| B 服务端工程 | 70 | 70 | **70** | 0 | 5 个 >1500 行文件行数不变（`splitlines` 口径 2619/2044/1885/1811/2459；`wc -l` 同值±1 为末行换行差异），B 70 硬条件继续命中 |
| C 前端交互 | 86 | 86 | **86** | 0 | `git diff 61454c9..HEAD -- frontend/` 仅 `node_modules` 外零产品改动（见 §3-C） |
| D 安全合规 | 85 | 90 | **90** | 0 | 应用侧加固未回退；平台层 `Access-Control-Allow-Credentials` 本轮 5 次探测均未复现（R2 曾见 true）→ P1-1 降级为 P2，但不构成加分项（缺「不可信包裹」仍在） |
| E 产品闭环 | 90 | 90 | **90** | 0 | 产品逻辑零改动 |
| F 测试可验证 | 82 | 100 | **100** | 0 | CI 连续绿（run 13/14/15），run 15 = 被评提交且三 job 全绿 |
| G 文档一致性 | 90 | 86 | **85** | **−1** | 874/878 数字偏差（8 处，4 文件）+ 2 处旧矛盾 + 1 处 UI 描述不符 + 1 处步骤数矛盾 |
| H 可运维性 | 92 | 92 | **97** | **+5** | 请求号贯通落地（代码/测试/线上/部署一致）+ 部署自检 + keep-warm；扣 3 分见 §3-H |
| **合计** | 85.95 | 88.30 | **88.50** | **+0.20** | — |

**一句话判读**：本批把「日志无 request_id」（P1-2）和 H 维的唯一缺口真正关掉了（+5 H，实测可复现），并顺手修了 CI（已在 R2 关）；但**唯一能大幅抬分的 B 维（5 个大文件）与唯一的 P0（持久化）零动作**，G 又因新数字错误小幅回落 → 总分几乎原地踏步（+0.20），仍差 6.50 分与 1 个 P0。

---

## 3. 每维依据与证据

### A · 功能正确性与回归稳定性（20）→ **95**（沿用 R2，本轮复跑确认无回归）

- 后端全量两跑（同一条命令，独立两次）：
  - 第 1 跑：`Ran 878 tests in 78.155s` / `OK` / `0 skipped`（`$TEMP/r3_backend_tests.txt:849`）。
  - 第 2 跑：`Ran 878 tests in 80.752s` / `OK` / `0 skipped`（`$TEMP/r3_backend_tests2.txt`，`EXIT=0`）。
  - 两跑计数与结论完全一致 → 无 flaky、无顺序依赖、无遗漏 skip（841 → 878 = 新增 `test_log_context.py` 8 + `test_deploy_env_check.py` 25 + `test_pre_release_check.py` 净增 4）。
- 前端：`./node_modules/.bin/tsc.cmd --noEmit` → **exit 0**；`npm test` → `tests 334 / pass 334 / fail 0 / skipped 0 / duration_ms 24151`（`$TEMP/r3_frontend_tests.txt:339,341,342,344`）。
- 线上冒烟（只读、不登录）：`GET /backend/api/v2/quota` → **401 + 统一错误体** `{"error":{"code":"UNAUTHENTICATED","message":"请先使用邮箱登录","retryable":false,"request_id":"…"}}`（并保留旧前端兼容字段 `detail`）；`/backend/health`、`/health/ready`、`/health/live` 均 200；Web `/` 200（1.24s）、`/ops` 200（1.09s）。
- 为什么不是 100：A 100 要求「关键链路 API 冒烟 100%」独立复跑。本轮**未**跑「登录→BYOK 配置→建会话→提交命令→Run」认证链路（禁止调用真实模型、无验证码邮箱），只能沿用 R2 的既有证据 → 与 R2 同为 95。本轮新增中间件未引入回归（新 8 例覆盖中间件生成/沿袭/回显/错误体一致；旧错误体 `detail` 双写保留）。

### B · 服务端工程质量（15）→ **70**（沿用 R2；本轮新代码质量尚可，但硬条件未动）

- 5 个 >1500 行文件**行数与 R2 完全一致**（`splitlines` 口径）：
  - `backend/services/command_service.py` **2619**
  - `backend/workflows/runner.py` **2044**（R2 记 2045，差 1 为末行换行计法差异，文件本批未进 diff）
  - `backend/workflows/audit.py` **1885**
  - `backend/domain/contracts.py` **1811**
  - `frontend/store/sessionStore.ts` **2459**
  - 另发现 2 个 R2 未列名的 >1500 行文件：`frontend/app/session/[id]/loop/page.tsx` **1704**、`backend/tests/test_audit_workflow.py` **1568**（测试文件亦计入「单文件 ≤800 行」的工程口径）。
- 本轮新增/重写模块的复杂度：`backend/core/log_context.py` 72 行（ContextVar + Filter，单一职责）、`scripts/check_deploy_environment.py` 283 行（7 个 check 函数，纯标准库）、`scripts/pre_release_check.py` 433 行（步骤表驱动）——**均 <800 行，未引入新的巨型文件**；`backend/main.py` 602 行（较 R2 +21）。
- 其它 B 项复核（无回退）：路由层无 SQL（未变）；写操作事务/幂等未变；错误码仍可区分（401 `UNAUTHENTICATED` / 428 `LLM_CONFIG_REQUIRED` / 409 `LOOP_VERSION_CONFLICT`，本轮线上 401 再次实测）；`shell=True` 仅 2 处（`scripts/pre_release_check.py:220,232`，Windows 下调 `npm`/`.cmd`，命令由常量拼装、不含用户输入，R2 已记录，非注入面）。
- **结论**：新增模块没有带来新的复杂度问题（这点是正向的），但 B 70 的硬条件是「存在 >1500 行文件」，5 个文件零拆分 → 不能脱离 70。重构/新增脚本本身**不加分**（rubric 误判提醒）。

### C · 前端与交互质量（15）→ **86**（沿用 R2，产品代码零改动）

- 证据边界：`git diff --stat 61454c9..eeee88f -- frontend` 只有 `docs/browser-qa/e2e/login43-live-same-origin.png`（文档截图）命中路径前缀，**`frontend/app`、`frontend/components`、`frontend/lib`、`frontend/store`、`frontend/hooks` 零改动**（B/A 之外的产品代码也未改）。
- 沿用 R2 的实测锚点：`git grep dangerouslySetInnerHTML|.innerHTML=` 0 命中；三态/`aria` 命中 68 处；冷首屏 FCP 3128ms（单样本，>2s）；构建 12 条路由；`audit` 页 12.9 kB；无 localStorage/Authorization token，鉴权唯一入口为 Cookie。
- 本轮未用浏览器复测 console error（见 §8 无法核实），但零产品改动使其不变。
- **结论 86 不变**：三态大体完整（少数视图缺空态）但冷首屏 >2s，且本轮没有针对 C 的改进动作。

### D · 安全与合规（15）→ **90**（维持；平台层风险实测下降，但不加分）

- 应用侧（R2 关闭项，本轮复核仍在）：`backend/core/config.py:26-56` `_parse_cors_origins` 丢弃 `*` 且 warning `event=cors.wildcard_origin_ignored`；`backend/main.py:83-97` 白名单 + `allow_credentials=True`；`backend/tests/test_platform_readiness.py:105-133` CorsPolicyTests 4 例。
- 生成内容标识：`generated_by: Literal["model","fallback"]` 仍在；日志脱敏：本轮新增代码只输出**长度/主机/来源名**，不输出密钥值（实测 `--json`："AUTH_SECRET_KEY 长度 69（来源：AUTH_SECRET_KEY）"、"SMTP 端口可达：smtp.163.com:465"）→ 无新增泄露面。
- **平台层 P1-1 实测变化（重要）**：R2 的六条 curl 证据里平台代理注入 `access-control-allow-credentials: true`。本轮 5 次探测**均未再出现**该头：
  - `GET /backend/api/v2/quota -H "Origin: https://evil.example"` → 有 `Access-Control-Allow-Origin: https://evil.example` + `Access-Control-Expose-Headers: *`，**无 ACAC**；
  - `OPTIONS` 预检（evil origin + `Access-Control-Request-Method: POST`）→ `Allow-Origin: https://evil.example`、`Allow-Methods: POST`、`Max-Age: 600`，**无 ACAC**；
  - 白名单 Origin（`https://www.brainstorming.top`、`https://pm-brainstorming-workbench.vercel.app`）、无 Origin 请求同样无 ACAC。
  - 影响判读：无 ACAC 时浏览器会拒绝跨源带凭据（`credentials:"include"`）的响应读取 → R2 的「凭据型跨域读取」风险**不可复现**；但 ACAO 反射 + `Expose-Headers: *` 仍在（平台侧，Vercel/HF 代理注入），`Origin` 任意值被回显属平台层配置问题 → **P1-1 降级为 P2**（并标注回归风险：平台配置可随时改回）。
- 仍未关闭的 D 维核心缺口（未改代码）：**「外部文本进模型前包裹为不可信内容」仍 0 命中**（`backend/core/role_prompts.py:189` 仅一句边界提示）→ 维持 D=90（rubric D100 要求该条成立）。Cookie `secure` 默认 False（`backend/core/config.py:130`）亦维持（本地开发态，P2）。
- 新增小项（P2 加固）：调用方传入的 `X-Request-ID` 未做长度/字符集收敛（`backend/main.py:108-111` 只 `.strip()`）。本地 TestClient 实测可回显 5000 字符、含换行值；真实 HTTP 客户端无法注入 CRLF（框架/代理拒绝），故只按 P2 记录。

### E · 产品逻辑与体验（15）→ **90**（沿用 R2，产品逻辑零改动）

- 本轮没有任何产品/业务代码改动（diff 只含 ops/测试/文档）→ 7 步链路、「断点兜底」、文案诚实度、指标唯一性全部沿用 R2 结论与证据（`docs/full-journey-e2e.md` 真模型记录、`frontend/lib/api.ts:536-542`、`frontend/lib/sse.ts:247-252`、`frontend/store/sessionStore.ts:448-483`、`ConnectionBanner.tsx`）。
- 未逐页实测「每步下一步入口」→ 不能给 100；也无新增缺陷 → 不降。**90 不变**。

### F · 测试与可验证性（10）→ **100**（维持；被评提交自身 CI 全绿）

- CI REST（`/actions/runs?branch=main&per_page=4`，`total_count=23`）：
  - run **15**（id `45932843781`）head **`eeee88fa`** → `conclusion=success`，created `2026-11-27T03:12:56Z`，name `ci`；
  - run 14（`1a487d50`）`ci` success；run 13（`99c0f7ab`）`ci` success；run 1（`1a487d50`）`keep-warm` success。
- run 15 step 级：`backend (python 3.12)` **10/10 step success**；`browser e2e (AC-01/19/31, real Chrome)` **16/16**；`frontend (node 20)` **14/14**；合计 **40/40**，无失败/取消步骤。
- 套件时长：后端 78.2s（<5min）、前端 24.2s；三 job 并行；契约 parity 13 例（R2 证据，本批未改契约层）。
- 新增测试本身的独立性：`backend/tests/test_deploy_env_check.py` 用 `unittest.mock` 注入 TCP 探针（`test_smtp_unreachable_fails` / `test_smtp_reachable_ok` 均 mock），**不依赖外网**；`test_log_context.py` 用 TestClient，不依赖网络。
- 保持 100：F 100 的六条（核心服务直接测试、关键契约断言、不依赖网络、可并行、CI 跑过、套件 <5min）全部满足；本批新增 44 例提升覆盖。
- 附注（不扣分）：`check_deploy_environment.py` 的 25 例全部用 `redirect_stdout(StringIO)` 走 `main()`，**没有一条以真实子进程跑默认文本输出**——这正是 §6 P2-1（GBK 崩溃）逃过测试的原因。测试盲区值得补 1 例（subprocess + `PYTHONIOENCODING` 缺省）。

### G · 文档与交付一致性（5）→ **85**（R2 86 → −1）

扣分明细（rubric「每处虚假声明 −2」，同类合并后仍从严）：

| # | 问题 | 位置 | 扣分 |
| --- | --- | --- | --- |
| 1 | **后端用例数写 874，实测 878（两跑一致）** | `docs/v4-acceptance-report.md:30,167`；`docs/v4-release-checklist.md:4,9`；`docs/v4-release-notes.md:11,101`；`docs/loop-runs/README.md:285,350`（8 处 / 4 文件） | −5 |
| 2 | 旧自相矛盾仍在：报告来源 `fallback` vs 断言 `source=model` | `docs/full-journey-e2e.md:31` vs `:38` | −2 |
| 3 | 旧自相矛盾仍在：「未进 CI」vs「CI 里有一个独立 job」 | `docs/browser-qa.md:114-115` vs `:178` | −2 |
| 4 | 「错误体 request_id（前端提示里会带出来）」与代码不符：`frontend/lib/api.ts:314` 只把它存进 `ApiError`，**无任何组件渲染/提示**（`git grep requestId frontend/components frontend/app` 仅 ChatPanel 复用作幂等键，0 处展示） | `docs/v4-release-checklist.md:119` | −2 |
| 5 | 体检步骤数自相矛盾：同文件「8 步」vs「9 步」（实际 `pre_release_check.py:43-53` 为 9 步） | `docs/loop-runs/README.md:350` vs `:285` | −2 |
| 6 | 平台层限制描述与实测有出入（写「Vercel 只保留它自己的 X-Proxied-Path/X-Proxied-Replica」，实测响应头仍有 `X-Request-Id` 且与错误体一致；该句在未推送的 `bcfea41` 中，不计入被评提交） | `docs/v4-release-checklist.md:136`（`bcfea41`） | 0（不计） |

合计 −13 → **85**。正向项（不抵分，仅说明文档确实在维护）：本批新增 §6.7「请求号贯通与部署变量自检」（`docs/v4-release-checklist.md:114-133`），把 request-id 三处出口（响应头 / 错误体 / 日志 `[rid=]`）与部署自检 7 项写清楚；`README.md:360-361` 把部署自检加进「发版前一键体检」清单；`fixes 837→841、路由 7→12` 在 `99c0f7a` 已修（本轮复核 `docs/v4-release-notes.md:13` 已是 12 条路由）。

**判读**：874 这个错不是「口径不同」，而是作者把 841+8+25 算了、漏掉 `test_pre_release_check.py` 净增的 4 例；同一错误被复制到 8 处，属 R2 同型问题（R2 是 837 写 7 处）→ G 不进反退。

### H · 可运维性（5）→ **97**（R2 92 → **+5**，本批实质改进）

rubric H100 五条件逐条核实：

| 条件 | 实测 | 判定 |
| --- | --- | --- |
| 启动前有前置检查 | `scripts/container_start.py` 起 Worker 前先幂等建表并对失败返回非零（`_prepare_database` 早于 `_spawn`）；发版侧 `scripts/pre_release_check.py:43-53` 把 `deploy-env` 插为第 2 步，`fail` 直接 `PRE_RELEASE_FAILED` | ✅ |
| health 区分存活/就绪且给 blocking_reasons | 线上：`/health/live` 200（Content-Length 15）、`/health/ready` 200 `{"status":"ok","blocking_reasons":[],"warnings":[…]}`、`/health` 200 带 `warnings`；代码 `backend/main.py` 三端点未改 | ✅ |
| **日志带关联 id**（R2 唯一缺口） | `backend/core/log_context.py`（72 行，ContextVar+Filter）+ `backend/main.py:41-45`（`[rid=%(request_id)s]` 格式 + `install_request_id_filter()`）+ `:99-136`（中间件生成/沿袭 id → `request.state`/日志上下文/响应头）；本地两跑实证日志行 `INFO [services.command_service] [rid=cef709bde20b456eba141dfac97367ae] …`，非请求上下文回落 `[rid=-]`；线上带 `X-Request-ID: probe_r3_test_0001` → 401 响应头 `X-Request-Id: probe_r3_test_0001` + 错误体 `request_id: probe_r3_test_0001`；部署的 `main.py`/`log_context.py` 与本地 HEAD 逐字节一致 | ✅ |
| 迁移可重复 | 沿用 R2：`backend/repositories/database.py:773,797-862` 幂等 + `backend/tests/test_schema_upgrade.py:99`；本批未改 | ✅ |
| 失败退出码正确 | `scripts/container_start.py:160` 子进程退出即整体非零；`pre_release_check.py` 0/1/2；本轮实测 `--only deploy-env` → `EXIT=0`，`[OK] deploy-env 0.2s` | ✅ |

额外加分动作（不改变锚点，但属真实改进）：`keep-warm.yml` 每小时 `GET /backend/health`（3 次重试、15s 间隔），run 1 实测 success，降低免费 Space 冷启动/休眠叠加（但不改变数据持久性）。
**扣 3 分理由（−3 → 97）**：新增的部署自检脚本 `scripts/check_deploy_environment.py` 的**默认文本输出在 Windows GBK 控制台崩溃**：
- PowerShell 复现（README:361 给出的正是该命令）：`traceback … line 276, in main / print(render_text(checks)) / UnicodeEncodeError: 'gbk' codec can't encode character '\u2713' in position 177`，`PSEXIT=1`；
- bash 管道复现同样崩溃；
- 根因：`render_text`（`:242-252`）用 `✓/!/✗` 全角符号，`main()`（`:276`）直接 `print()`，未做 UTF-8 重配；
- 影响面：发版体检走 `--json`（`pre_release_check.py:160-187` 取最后一行 `json.loads`）**不受影响**，实测 `--only deploy-env` 通过；但「运维手册里那条命令在本机开发 OS 上不可用」是可运维性的真实小缺口，故不给 100。

---

## 4. 本轮「声称项」独立核实表

| 声称 | 独立核实 | 结论 |
| --- | --- | --- |
| ① 请求号贯通：`log_context.py` + 中间件 + 日志格式 + 错误体 + 响应头；8 例测试 | 代码逐行核对 + 8 例单测 + 本地两跑日志实证 + 线上 401 回显（header 与 body 同 id） | ✅ **成立**（注意 header 大小写为 `X-Request-Id`；浏览器侧因应用未配 `expose_headers` 读不到该头，但错误体可用） |
| ② 部署变量自检 7 项 + 接入体检第 2 步；25 + 11 例测试 | `--json` 实跑 exit 0（7 项：database warn / auth ok / cookie ok / cors ok / email ok / llm ok / storage warn）；`--only deploy-env` exit 0 0.2s；步骤序 `docs → deploy-env → backend-import …`（9 步） | ✅ 成立；但**默认文本输出在 Windows 崩溃**（新增 P2-1） |
| ③ CI 已覆盖 main 且全绿（历史 run 4 个） | run 15 = `eeee88f` success；run 14/13 success；run 1 keep-warm success；step 级 40/40 | ✅ **成立** |
| ④ 线上 Vercel 前端 + HF Space `sha=f4fbdcf` RUNNING；/health 带持久化 warnings；live/ready 200 | Space API：`sha=f4fbdcf…`、`stage=RUNNING`、`cpu-basic`、`lastModified 2026-11-27T03:15:09Z`；`/health` warnings 逐字含「运行在 Hugging Face Space 且未设置 DATABASE_URL…」；`/health/live`、`/health/ready` 200；部署文件 sha256 与 HEAD 一致 | ✅ **成立** |
| ⑤ 测试基线「后端 874 / 前端 334」 | 前端 ✅ 334/334 + tsc 0；后端 ❌ **878（两跑一致）**，874 属漏算 4 例 | ⚠️ **部分不实** → 计入 G |

---

## 5. P0-1 专项判断：**仍未关闭**（这是本卡判 FAIL 的第二条闸门）

被测事实（全部可复跑）：

1. `GET https://www.brainstorming.top/backend/health` → `{"status":"ok","warnings":["运行在 Hugging Face Space 且未设置 DATABASE_URL：SQLite 位于容器内，实例重启或休眠回收后数据会丢失（请挂持久卷或改用外部数据库）"]}`。
2. `GET /backend/health/ready` → 200、`blocking_reasons: []` —— **就绪探针认为没有阻塞项**，尽管数据会丢。
3. Space 运行时 `cpu-basic`、无持久卷；`runtime.gcTimeout=172800`（48h 空闲回收），重启即回干净库。
4. 本轮新增的部署自检 `scripts/check_deploy_environment.py:90-…` 对「无 `DATABASE_URL`」判 **WARN 而非 FAIL**；`overall_exit_code`（`:238-240`）只有 FAIL 才返回 1 → 本机实跑 `ok:true`、exit 0。文档也把它固化为放行项：`docs/v4-release-checklist.md:126`「无 URL 只记 warn（SQLite 会随重启丢）」。
5. 披露仍在：`README.md:53-57`、`docs/v4-release-checklist.md:150,175`。

按 rubric 判：P0 = **数据损坏**（这里是「重启/休眠回收丢全部业务数据」，等价后果）。rubric 明写「封顶只在评测组确认 P0 仍存在时生效；**「已缓解/部分修复」不算关闭；P0 关闭必须可复跑证据**」。

- 披露 ≠ 关闭：warnings 只是让运维知道，没有阻止数据丢失；对用户而言，会话/消息/运行记录在 Space 回收后**全部消失**，这是产品级数据事故。
- 本轮新动作反而把 P0 **制度化**：自检把该配置判为 warn（不阻断），`/health/ready` 的 `blocking_reasons` 为空（不阻断），体检第 2 步 warn 通过（`PRE_RELEASE_OK`）→ 三段闸门全部放行一个 P0 级配置。
- 关闭所需的可复跑证据（当前一条都不存在）：外部数据库或持久卷已挂 + 重启演练（建会话 → 重启/回收 → 带 Cookie 读取仍返回原会话）+ `/health/ready` 不再有持久化 warning + 自检对缺 `DATABASE_URL` 返回 FAIL。
- **结论：P0-1 仍未关闭 → 命中封顶 89 + 判 FAIL。**（若后续要按「演示站可接受」口径豁免，必须先正式修改 rubric 的 P0 定义或由产品/运维书面接受，评测组不能自行改权重/口径。）

---

## 6. P0 / P1 / P2 清单（R3 状态）

### P0（未关闭 → 封顶 89 + 判 FAIL）
- **P0-1 线上 SQLite 无持久化（R2 → R3 仍未关闭）**：证据见 §5；线上 `warnings`、`blocking_reasons: []`、自检 warn/exit 0、`checklist:126` 放行口径。

### P1（本轮已全部关闭或无仍未关闭项）
- **P1-2 日志无 request_id → ✅ R2→R3 已关闭**：`backend/core/log_context.py`（72 行）+ `backend/main.py:41-45,99-136`；`backend/tests/test_log_context.py` 8 例；本地两跑日志 `[rid=…]` 与 `[rid=-]` 回落；线上 401 header+body 同 id；部署代码逐字节一致。
- **P1-3 CI 恒红 → ✅ 已关闭（R2 关，R3 保持）**：run 15（被评提交）三 job 40/40 success。
- **P1-1 平台侧 CORS 凭据反射 → ⚠️ 降级为 P2**：应用侧 R2 已关；R2 观测到的 `Access-Control-Allow-Credentials: true` 本轮 5 次探测**均未复现**（ACAO 反射 + `Expose-Headers: *` 仍在，平台侧 Vercel/HF 注入）。仍属部署方缺陷，存在回归风险。

### P2（可挂账，按影响排序）
- **P2-1（本轮新增）部署自检文本模式在 Windows GBK 下崩溃**：`scripts/check_deploy_environment.py:242-252,276` `print(render_text())` → `UnicodeEncodeError '\u2713'`；PowerShell 实测 traceback + `PSEXIT=1`；`README.md:361` 文档命令不可直接用；`pre_release` 走 `--json` 不受影响（实测 0.2s OK）。验收：`PowerShell 直跑 README 命令 exit 0 且中文/符号正常`。
- **P2-2 文档数字与事实不符**：874 vs 878（8 处 / 4 文件）；体检「8 步」vs 9 步；「前端提示会带出 request_id」与实际不符（`checklist:119`）。验收：`grep -rn "874" docs README.md` 0 命中且数字与 `Ran 878 tests` 一致。
- **P2-3 请求号未做长度/字符集收敛**：`backend/main.py:108-111` 仅 `.strip()`；实测 5000 字符原样回显、换行值在 TestClient 被接受 → 日志注入/刷屏的理论面（真实 HTTP 客户端难以注入 CRLF）。验收：rid 限长（如 ≤128）+ 仅允许 `[A-Za-z0-9_-]`，越界则生成新 id。
- **P2-4 平台层 ACAO 反射 + `Access-Control-Expose-Headers: *`**：Vercel/HF 注入，任意 Origin 被回显（无 ACAC，故无凭据读取）。归属部署方；验收：平台侧移除反射或前端改同源直连并关闭跨域。
- **P2-5 5 个 >1500 行文件 + 2 个额外 >1500 行文件**（见 §3-B），B 维 70 的唯一硬条件。
- **P2-6 `backend/` 遗留 28 个空 `runner-test-*` 目录**（R2 记 12 个 → 本批增长到 28，`docs/qa-loop-runs/round-5.md:24` 曾记 125 个）；测试卫生问题，建议 CI 收尾加清理。
- **P2-7 `auth_cookie_secure` 默认 False**（`backend/core/config.py:130`，only-local 开发态）、**无 CSRF token**（SameSite=lax 缓解）——沿用 R2，未变。
- **P2-8 前端未展示 request_id**：`frontend/lib/api.ts:314` 已存进 `ApiError`，但无组件渲染（0 处）。若目标是「用户报障把 id 给运维」，需在错误提示处展示（或明确告诉用户去 Network 面板看）。
- **P2-9 冷首屏**：FCP 3128ms（R2 单样本）；线上 `/` TTFB 1.24s（本轮 curl 总时长，不等价 FCP）——C 维 86 的主因之一。

---

## 7. 距 PASS 还差什么（按杠杆大小排序，附可验证验收条件）

**差额**：95 − 88.50 = **6.50** 加权分（且必须先解除 P0 的 89 封顶）。

| 杠杆 | 动作 | 加权收益 | 验收条件（可复跑） |
| --- | --- | ---: | --- |
| **0. P0-1 关闭（前置）** | 外部 DB（Postgres）或 HF 持久卷；并把「无 `DATABASE_URL`」从 warn 改 FAIL（或让 `/health/ready` 的 `blocking_reasons` 非空） | 0（解除封顶 89；不加分） | 重启/回收演练：建会话 → 重启 Space → 带 Cookie `GET /api/v2/sessions` 仍返回该会话；`/health/ready` 无持久化 warning 且 `blocking_reasons` 为空；`check_deploy_environment` 对缺失 URL 返回 **FAIL/exit 1**；重启前后数据条数截图/JSON 证据入库 |
| **1. B 维（最大杠杆）** | 拆 5 个 >1500 行文件到 **≤800 行**（先脱离 70 硬条件，目标 B 90–100） | **+3.00（B=90）～ +4.50（B=100）** | `wc -l` 五个文件均 ≤800（`find … | awk '$1>1500'` 为空）；拆分后后端 878 例 + 契约 parity 13 例全绿、CI run 绿；接口错误码不变（401/428/409 冒烟） |
| **2. C 维** | 首屏可交互 <2s（P75，真机 5 次采样中位数）+ 补齐空态 + console 0 error | +0.90（C=92） | CDP 采集 5 次 LCP/FCP 中位数 <2s；`cdp` 脚本断言 `console` 无 error；空态清单逐页截图 |
| **3. A 维** | 关键链路（登录→BYOK→建会话→命令→Run）API 冒烟脚本化并 100% pass（可 stub 模型） | +1.00（A=100） | 一条命令产出 `SMOKE_OK n/n`，CI 或 pre-release 里跑；失败即非零 |
| **4. G 维** | 874→878（8 处）、8 步→9 步、消 2 处旧矛盾、UI 描述改准 | +0.75（G=100） | `grep -rn "874" docs README.md` 为空；`docs` 内数字与 `Ran 878 tests` 一致；`docs/full-journey-e2e.md` 的 fallback/model 描述一致；`browser-qa.md` CI 说法一致 |
| **5. D 维** | 外部文本进模型前包裹为不可信内容（prompt 侧边界的结构化实现）+ 提供证据测试 | +0.30（D=92） | 新增 1 例测试断言外部文本被包在 `untrusted_content` 边界内；`git grep` 有实现与用例 |
| **6. H 维** | 自检脚本文本输出 UTF-8（`sys.stdout.reconfigure(encoding="utf-8")` 或纯 ASCII 符号） | +0.15（H=100） | PowerShell 直跑 `README.md:361` 命令 exit 0、中文与符号正常 |
| **7. E 维** | 逐页补「下一步入口」并留证据 | +0.75（E=95） | 7 步每步有截图/断言，缺一即失败 |

**组合可行性**：0 + 1(B=93) + 2(C=92) + 3(A=100) + 4(G=100) + 6(H=100) = +3.45+0.90+1.00+0.75+0.15 ≈ **+6.25 → 94.75**，仍差 0.25；因此「PASS 事实上要求 B 拆到 ≤800（B≈100）或同时拿 C/D/E 的增量」。**最省的路径是：关 P0 + 拆 5 个大文件 + 首屏提速 + A 冒烟脚本化**，四项到位可到 ~95.4。

---

## 8. 无法核实项（明确声明，不假设通过，不据此抬分）

| 项 | 原因 | 本卡处理 |
| --- | --- | --- |
| CI 后端 job 的 Linux 侧 `Ran N tests` | GitHub job logs 需仓库 admin：`GET /actions/jobs/140289191177/logs` → `HTTP 403 "Must have admin rights to Repository."` | 以本地两跑 **878** 为准；未发现任何平台条件测试/`skip`，Linux 计数应为一致 878 |
| 线上服务端日志是否真的每行带 `[rid=…]` | HF Space 运行日志需登录/付费权限，无法读取 | 用三重替代证据：部署 `log_context.py`/`main.py` sha256 与本地 HEAD 一致 + 本地两跑日志实证 + 线上 401 header/body 同 id |
| Vercel 前端部署 sha | 未提供部署元数据 | 前端产品代码自 R2 起零 diff（`git diff 61454c9..eeee88f -- frontend/app frontend/components frontend/lib frontend/store frontend/hooks` 为空），故 C 结论不受影响 |
| 前端 console error / 首屏 FCP 本轮复测 | 本轮无浏览器与 CDP 驱动（只读命令 + 无模型调用前提下） | 沿用 R2 证据（FCP 3128ms、console 无 error），C 保守保持 86 |
| 真实登录/生成链路 | 禁止调用真实模型 + 无验证码邮箱 | 沿用 R2 证据（A 保守保持 95） |
| 平台层 ACAC 是否永久消失 | 平台配置可随时变更（本轮 5 次探测均无） | 记为「本轮不可复现」并降级 P1→P2，标回归风险 |

---

## 9. 可复跑命令与关键输出（本轮实际执行）

### 9.1 本地（只读）

```bash
# 后端全量（两跑，独立各一次）
cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests -t .
# → Ran 878 tests in 78.155s / OK / 0 skipped（第 1 跑）
# → Ran 878 tests in 80.752s / OK / 0 skipped（第 2 跑，EXIT=0）
#   日志实证：INFO [services.command_service] [rid=cef709bde20b456eba141dfac97367ae] …
#              INFO [workers.run_worker] [rid=-] / [httpx] [rid=-]

# 前端
cd frontend && ./node_modules/.bin/tsc.cmd --noEmit        # exit 0
cd frontend && npm test                                     # tests 334 / pass 334 / fail 0 / skipped 0 / 24151ms

# 部署自检（json 通路，发版体检实际用的就是它）
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/check_deploy_environment.py --env-file backend/.env --json
# → exit 0；7 项：database warn（未设 DATABASE_URL）、auth ok（AUTH_SECRET_KEY 长度 69）、cookie ok、
#   cors ok（白名单 4 来源）、email ok（smtp.163.com:465 可达）、llm ok（BYOK）、storage warn

# 部署自检（默认文本通路 → Windows GBK 崩溃，P2-1 证据）
./.venv/Scripts/python.exe scripts/check_deploy_environment.py --env-file backend/.env
#   UnicodeEncodeError: 'gbk' codec can't encode character '\u2713' in position 177（scripts/…:276）
powershell -NoProfile -Command "& '.\\.venv\\Scripts\\python.exe' 'scripts\\check_deploy_environment.py' --env-file 'backend\\.env'"
#   traceback 同上 + PSEXIT=1

# 体检第 2 步单步实跑
./.venv/Scripts/python.exe scripts/pre_release_check.py --only deploy-env
# → [OK  ] deploy-env  0.2s  7 项 / 失败 0 / 警告 2（database, storage）  /  PRE_RELEASE_OK 1 步

# 行数（B 维）
for f in backend/services/command_service.py backend/workflows/runner.py backend/workflows/audit.py \
         backend/domain/contracts.py frontend/store/sessionStore.ts; do wc -l "$f"; done
# → 2619 / 2044 / 1885 / 1811 / 2459
# 另：frontend/app/session/[id]/loop/page.tsx 1704、backend/tests/test_audit_workflow.py 1568
```

### 9.2 线上只读探测（2026-11-27 11:20–11:31 +08）

```bash
curl -i -H "X-Request-ID: probe_r3_test_0001" https://www.brainstorming.top/backend/api/v2/quota
# HTTP/1.1 401 ; X-Request-Id: probe_r3_test_0001
# body: {"error":{"code":"UNAUTHENTICATED","message":"请先使用邮箱登录","retryable":false,
#        "request_id":"probe_r3_test_0001","details":{}},"detail":"请先使用邮箱登录"}

curl -s https://www.brainstorming.top/backend/api/v2/quota            # 不带 header
# body request_id = "eLVQ-5"，响应头 X-Request-Id: eLVQ-5（平台注入 id 被应用沿用 → 头/体一致）

curl -s https://www.brainstorming.top/backend/health
# {"status":"ok","warnings":["运行在 Hugging Face Space 且未设置 DATABASE_URL：SQLite 位于容器内，
#  实例重启或休眠回收后数据会丢失（请挂持久卷或改用外部数据库）"]}
curl -s -i https://www.brainstorming.top/backend/health/ready
# 200 {"status":"ok","blocking_reasons":[],"warnings":[同上]}
curl -s -i https://www.brainstorming.top/backend/health/live                 # 200, Content-Length: 15

# 平台层 CORS（5 次探测均无 ACAC）
curl -s -D - -o /dev/null .../api/v2/quota -H "Origin: https://evil.example"
# Access-Control-Allow-Origin: https://evil.example ; Access-Control-Expose-Headers: * ;（无 ACAC）
curl -s -D - -o /dev/null -X OPTIONS .../api/v2/sessions -H "Origin: https://evil.example" \
     -H "Access-Control-Request-Method: POST"
# 200 ; Allow-Origin: https://evil.example ; Allow-Methods: POST ; Max-Age: 600 ;（无 ACAC）

# Web
curl -o /dev/null -w "%{http_code} %{time_total}s" https://www.brainstorming.top/       # 200 1.243s
curl -o /dev/null -w "%{http_code} %{time_total}s" https://www.brainstorming.top/ops    # 200 1.092s
```

### 9.3 CI / Space（只读 REST）

```bash
curl -s -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/daniu-star/pm-brainstorming-workbench/actions/runs?branch=main&per_page=4"
# run 15  eeee88fa success 2026-11-27T03:12:56Z ci    ← 被评提交
# run 14  1a487d50 success ci ; run 13 99c0f7ab success ci ; run 1 1a487d50 keep-warm success
curl -s .../actions/runs/45932843781/jobs
# backend 10/10 step success ; browser-e2e 16/16 ; frontend 14/14（合计 40/40，无失败步骤）

curl -s "https://huggingface.co/api/spaces/123chenjunliang/pm-brainstorm-workbench"
# sha=f4fbdcf45953dec7590cac006d5691965054ffde ; stage=RUNNING ; cpu-basic ;
# lastModified 2026-11-27T03:15:09Z ; domains …stage READY
curl -s "https://huggingface.co/spaces/123chenjunliang/pm-brainstorm-workbench/raw/main/backend/main.py"
curl -s "https://huggingface.co/spaces/123chenjunliang/pm-brainstorm-workbench/raw/main/backend/core/log_context.py"
# sha256 与本地 backend/main.py、backend/core/log_context.py 完全相同（部署=被评代码）
```

### 9.4 请求号实现证据（代码）

- `backend/core/log_context.py:17` `_current_request_id: ContextVar[str|None]`；`:23` `new_request_id(prefix="req")` → `req_`+12 hex；`:49-53` `RequestIdFilter.filter` → `record.request_id = … or "-"`；`:55-65` `install_request_id_filter` 幂等挂到 root handlers。
- `backend/main.py:41-45` `logging.basicConfig(format="… [rid=%(request_id)s] …")` + `install_request_id_filter()`；`:99-136` 中间件：`X-Request-ID`/`Idempotency-Key` → `request.state` → `bind_request_id` → 401 `error_payload(request_id=…)` → `finally reset` → `response.headers.setdefault("X-Request-ID", request_id)`。
- `backend/api/errors.py:30-40` `request_id_of()`（state → 头 → 空串），`:84,118,145,160` 各错误处理器统一带 id；`backend/domain/errors.py:140-146` `to_body(request_id=…)`。

---

## 10. rubric §6 五问（本轮速答）

1. **最可能先崩的地方**：HF Space 休眠/重启 → SQLite 全丢（P0-1，非「会不会」而是「何时」）；其次是 5 个大文件里任一处的改动回归（每改 2619 行文件一次就赌一次全量 878 例）。
2. **证据最弱的结论**：C/E 两维本轮无法独立复测（无浏览器、无真模型），只能沿用 R2；已在 §8 声明。
3. **若只给一天**：先做 P0-1 的「阻断化」（自检 FAIL + ready blocking_reasons），再做 B 的第一个文件拆分（`command_service.py` 2619 → 目标 ≤800），这是两个唯一能改变结论的动作。
4. **是否被营销话术带走**：核对了「CI 全绿」「请求号贯通」「Space f4fbdcf RUNNING」「测试 874」四句 —— 前三句成立，第四句不实（878）。
5. **反作弊检查**：本批无删断言（878 例比 R2 多 37 例）、无「只改文档口径」（P0-1 仍披露为限制而非宣称已修）、无「沙盒写成真实可用」（keep-warm/自检都标注了限制）。

---

## 11. 独立复核签字

- 评测者：`score-r3`（独立评测组子代理，非修复作者；只读代码/命令/网络，未改产品代码、未 commit/push、未调用真实模型、未消耗额度）。
- 签字结论：**不予签字（FAIL）**。理由：5 条闸门中「总分 ≥95」与「P0 清零」两条不满足，按 rubric §4 不得通过。
- 复核口径声明：本卡所有分数均附 `文件:行号` 或可复跑命令与输出；无法核实项已在 §8 显式剔除/保守处理，未为「好看」抬分，也未因缺信息无理压分（G/H 的扣分均给出具体位置与复现路径）。

---

### 附：本轮 R3 与 R2 的差异一句话总结

> 本批把「日志无 request_id」真正关掉了（H 92→97，线上可复现），顺手补了 44 例测试与 7 项部署自检；但唯一的 P0（线上持久化）与最大的失分项（B 维 5 个 >1500 行文件）**零动作**，而文档又把后端用例数写成 874（实测 878，8 处），使 G 86→85。净效果：88.30→**88.50**，仍 FAIL（差 6.50 + 1 个 P0）。
