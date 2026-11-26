# R2 评分卡（Loop 第 2 轮）— 独立评测组

- 评分人：`score-r2`（独立评测组子代理，**不是**修复作者；未改动任何产品代码、未 commit、未登录站点、未发验证码、未调用真实模型）
- 评分对象：`C:\Users\13067\Desktop\pm-brainstorming-workbench`，HEAD = **`61454c9c7c6c24e99218acef3ac025559bf0c288`（`61454c9`）**，`origin/main` 与 `ls-remote` 均为同一 sha；上游 R1 评分于 HEAD `866aaf3`（`loop/rounds/R1/scorecard.md:1`）。
- 基线漂移：`866aaf3..HEAD` 共 12 个提交（`08e754f`、`61a2075`、`abdbd73`、`5015713`、`22c715d`、`036b442`、`7394ec2`、`c07d9f5`、`7513837`、`eaa3e0e`、`a8ad00f`、`61454c9`），改动 21 文件 +607/−24，集中在 **CORS 应用侧加固、CI 引导/依赖/工作流、文档引用体检、文档数字、测试引导**；产品功能代码唯一改动是 `backend/services/loop_sandbox.py`（6+/2−，修 CI 沙箱 `killpg` 误杀 shell），**前端产品代码零改动**（`git diff --stat 866aaf3..HEAD -- frontend/app frontend/components frontend/lib frontend/store frontend/hooks` 为空）。
- 评分基准：`loop/rubric.md`（227 行，V1.0，冻结未改）。权重 A 20 / B 15 / C 15 / D 15 / E 15 / F 10 / G 5 / H 5；总分 = Σ(分×权重)/100（四舍五入到整数，**门限用未取整值**）。
- 时间窗：2026-11-27 01:34–01:55 +08（线上只读探测 01:44–01:50）；本地两套件、构建、CI REST 复核均在本轮重跑。
- 工作区状态：开测时 `git status --porcelain` 干净；取证中途曾出现 1 个**并发队友**产生的未跟踪文件 `docs/browser-qa/e2e/login43-live-same-origin.png`（非评测组创建，未触碰），收尾时该文件已不存在；最终 `git status --porcelain` 只剩 `?? loop/rounds/R2/`（本卡）。所有实测均在已提交 HEAD 上跑。

---

## 1. 一页结论：**FAIL**

| 项 | 值 |
| --- | --- |
| **总分** | **88.30 / 100（四舍五入 88）**（R1 = 85.95 → **+2.35**） |
| **结论** | **FAIL** |
| 未关闭 P0 | **1 个**（P0-1 线上无持久卷 → 重启/休眠回收丢 SQLite 全部业务数据；仍只有披露，无持久化落盘） |
| 封顶规则 | 命中 P0 → 总分封顶 89；本卡 88.30 < 89，封顶不额外生效（若后续加权分 >89 仍会被压到 89） |

| 维 | 分 | 权重 | 加权 | 锚点落点（一句话） |
| --- | --- | --- | --- | --- |
| **A 功能正确性与回归稳定性** | **95** | 20 | 19.00 | 沿用 R1；本轮复跑 841 两跑全绿一致、前端 334 全绿、0 skip；A 100 仍缺「关键链路认证流程 API 冒烟 100%」 |
| **B 服务端工程质量** | **70** | 15 | 10.50 | 沿用 R1：**5 个 >1500 行文件一个没拆**（2619/2045/1886/1812/2459），命中 B 70 锚点 |
| **C 前端与交互质量** | **86** | 15 | 12.90 | 沿用 R1：前端产品代码本轮零改动；无未转义拼接、三态大体完整；冷首屏 FCP 3.1s（>2s） |
| **D 安全与合规** | **90** | 15 | 13.50 | **重判**：应用侧「CORS 回显任意 Origin」单点绕过已关闭（代码+4 例测试+部署一致）；但**平台代理层**仍注入 ACAO/ACAC/expose-headers，且「外部文本包裹为不可信内容」仍缺失 → 90 |
| **E 产品逻辑与体验** | **90** | 15 | 13.50 | 沿用 R1：产品逻辑本轮零改动；7 步链路「每步下一步入口」仍未逐一实测 |
| **F 测试与可验证性** | **100** | 10 | 10.00 | **重判**：CI 在 HEAD 全绿（run `45867837383` 三 job step 级 success）、套件 <5min、三套件并行、契约 13 例、无外网依赖 |
| **G 文档与交付一致性** | **86** | 5 | 4.30 | **重判**：实测 841 但 4 组 7 处仍写 837、新增同文件 841/837 自相矛盾、2 处旧矛盾仍在、构建路由数 7 条已过期 |
| **H 可运维性** | **92** | 5 | 4.60 | 沿用 R1：health 三态/live-ready 分离/blocking_reasons/幂等迁移/退出码 ✅；**日志行仍不含 request_id**（logger 0 命中） |
| **合计** | — | 100 | **88.30** | **FAIL** |

### 硬闸门逐条（rubric §4）

| 闸门 | 要求 | 实测 | 结论 |
| --- | --- | --- | --- |
| 1 总分 | ≥95（未取整） | **88.30** | ❌ 差 **6.70** |
| 2 A | ≥95 | **95** | ✅ 满足（贴线） |
| 3 D | ≥90 | **90** | ✅ 满足（贴线；若把平台注入计入产品责任则 D=85，见 §5） |
| 4 未关闭 P0 | 0 个 | **1 个**（P0-1） | ❌ 不满足 |
| 5 独立复核签字 | 非修复作者 | `score-r2` 独立评测组 | ✅ 满足 |

**结论：FAIL（2 条闸门不满足：总分、P0）。**

---

## 2. R1 → R2 对比表

| 维度 | R1（866aaf3） | R2（61454c9） | Δ | 本轮依据 |
| --- | ---: | ---: | ---: | --- |
| A 功能正确性 | 95 | **95** | 0 | 沿用 R1，本轮复跑确认（841×2 全绿、334 全绿） |
| B 服务端工程 | 70 | **70** | 0 | 沿用 R1，本轮以 `python splitlines` 复测，5 文件行数**与 R1 完全一致**（无拆分） |
| C 前端交互 | 86 | **86** | 0 | 沿用 R1，前端产品代码本轮零 diff（`git diff` 空） |
| D 安全合规 | 85 | **90** | **+5** | 重判：应用侧通配符拒绝已落地并部署；平台层反射仍存在（不再算应用代码绕过），不可信包裹仍缺 |
| E 产品闭环 | 90 | **90** | 0 | 沿用 R1，产品逻辑零改动 |
| F 测试工程化 | 82 | **100** | **+18** | 重判：CI 在 HEAD 上真实跑绿（3 job/step 级 success），套件 <5min，历史两红根因已修 |
| G 文档口径 | 90 | **86** | **−4** | 重判：过期用例数从 3 组变 4 组、新增同文件矛盾、路由数过期 |
| H 可运维性 | 92 | **92** | 0 | 沿用 R1，日志仍无 request_id（0 命中） |
| **总分** | **85.95** | **88.30** | **+2.35** | 唯一加分项是 D(+5)/F(+18)，被 G(−4) 部分抵消 |

> 口径说明（避免"分数漂移"质疑）：
> 1. **没有改权重/锚点/系数**（rubric 未改）。R1 的 A/B/C/E/H 分数不是本轮"重置"来的，而是经"本轮复核无变化"后沿用。
> 2. **B 维行数口径**：`Get-Content ... | Measure-Object -Line` **会跳过空行**（同一文件 `command_service.py` 得 2354、`sessionStore.ts` 得 2237），本轮改用 `python -c "pathlib.Path(f).read_text().splitlines()"` 与 `git show HEAD:f` 复测，得 2619/2045/1886/1812/2459 —— **与 R1 完全一致**，证明 R1 的数字可复现、且本轮确实没有拆分。
> 3. **D 维不是"抬分"**：85 档的定义是"存在 1 处单点绕过"，而该单点（应用中间件回显任意 Origin）已有代码、单测、部署三重证据证明关闭；剩余缺口是另一类面（不可信内容包裹 + 平台层注入），按 rubric §1"先找最近档、再按扣分锚点调整"落到 90，并在 §5 给出反向敏感性（若按 85 记，结论仍是 FAIL）。

---

## 3. 每维依据与证据

### A · 功能正确性与回归稳定性（20）→ **95**（沿用 R1，本轮复核无变化）

复核证据（本轮实测）：

```powershell
cd backend; python -m unittest discover -s tests -t .
# 第 1 跑：Ran 841 tests in 73.847s / OK（%TEMP%\r2_backend_tests.txt）
# 第 2 跑：Ran 841 tests in 75.401s / OK（%TEMP%\r2_backend_tests2.txt，exit 0）
```

- 后端 841 例 / 两跑均 `OK` / 0 failed / 0 error / **0 skipped**（R1 为 837，本轮 +4 = 新增 CORS 策略 4 例）。
- 前端 `cd frontend; npm test` → `tests 334 / pass 334 / fail 0 / skipped 0`，exit 0（%TEMP%\r2_frontend_tests.txt）；`.\node_modules\.bin\tsc.cmd --noEmit` → exit 0（按任务要求不使用 `npx tsc`）。
- 契约断言 13 例（`backend/tests/test_contract_parity.py`，R1 已核，本轮文件未改）。
- 新增 841 中的 4 例可直接单跑验证：`python -m unittest tests.test_platform_readiness` → `Ran 16 tests in 0.223s / OK`（含 `CorsPolicyTests` 4 例 `ok`，日志可见 `WARNING [core.config] event=cors.wildcard_origin_ignored`）。

**为什么不是 100**：A 100 要求"关键链路 API 冒烟 100% 通过"。本轮受任务约束（禁止登录、禁止 POST 业务接口、禁止真实模型），只能覆盖健康三态 + 未鉴权默认拒绝（401 `UNAUTHENTICATED`）+ 前端全量，**未能跑通"建会话→澄清→brief→脑暴→审计→收口"的真实认证链路**（该链路只有 `docs/full-journey-e2e.md` 的 2026-11-24 历史记录）。这与 R1 的扣分理由相同，故沿用 95，不因测试数 837→841 而上调（数字增加不改变"冒烟覆盖不满"的事实）。

### B · 服务端工程质量（15）→ **70**（沿用 R1，本轮复核无变化）

锚点依据（rubric §2 B）：B 70 明列"**存在 >1500 行的文件**"。本轮实测命中，且**一个都没拆**：

| 文件 | HEAD 行数 | R1 行数 | 866aaf3 行数（git show） |
| --- | ---: | ---: | ---: |
| `backend/services/command_service.py` | **2619** | 2619 | 2619 |
| `backend/workflows/runner.py` | **2045** | 2045 | 2045 |
| `backend/workflows/audit.py` | **1886** | 1886 | 1886 |
| `backend/domain/contracts.py` | **1812** | 1812 | 1812 |
| `frontend/store/sessionStore.ts` | **2459** | 2376（Get-Content 口径）/2459（read 口径） | 2459 |

复跑命令：`python -c "import pathlib; [print(len(pathlib.Path(f).read_text().splitlines()), f) for f in [...]]"`；或 `git show HEAD:frontend/store/sessionStore.ts | python -c "import sys;print(len(sys.stdin.read().splitlines()))"`。

其余 R1 已核项本轮未变化：62 处宽泛 `except` 均带 `# noqa: BLE001` 与上下文日志（未检索到 `except Exception: pass`）；`backend/api/*.py` 路由层无 SQL；`scripts/container_start.py` 子进程生命周期与退出码回传正确；错误码可区分（401 `UNAUTHENTICATED`、428 `LLM_CONFIG_REQUIRED`、409 `LOOP_VERSION_CONFLICT` 等）。本轮唯一产品改动 `backend/services/loop_sandbox.py`（6+/2−，见 `git diff 866aaf3..HEAD -- backend/services/loop_sandbox.py`）修的是测试/开发用沙箱在 POSIX 上 `killpg` 误杀 CI shell 的问题，属正确性修复，**不构成 B 维加分**（rubric B 常见误判：重构/修复本身不加分）。

**为什么仍是 70**：rubric 的 85 档描述的是"少量宽泛异常且有上下文日志"，不改变"存在 >1500 行文件"这一 70 档硬条件；5 个文件仍远超上限（最大 2619），所以停在 70，未叠加 −3/文件的扣分锚点（与 R1 一致，避免双重扣分）。

### C · 前端与交互质量（15）→ **86**（沿用 R1，本轮复核无变化）

- **前端产品代码零改动**（决定性证据）：`git diff --stat 866aaf3..HEAD -- frontend/app frontend/components frontend/lib frontend/store frontend/hooks` → 输出为空；本轮前端改动只有 `frontend/package.json`（`node --test` glob 去引号，修 Node 20 不展开 glob）与新增 `frontend/tests/e2e/api-smoke.test.cjs`（CI 用），**均非产品代码**。
- 本轮重建核对：`git grep -n -E 'dangerouslySetInnerHTML|\.innerHTML[[:space:]]*=' -- frontend` → **0**（无未转义拼接面）。
- R1 的三态/aria/键盘/console 证据继续成立（`loop/rounds/R1/scorecard.md:104-116`）：`ops/page.tsx`、`settings/access/page.tsx:36-55`、`LlmConfigGate.tsx` 三态；`aria-label` 68 处；线上 `/login` 零 console error、skip-link 与 live region 存在。
- 本轮构建可用：`cd frontend; npm run build` → `✓ Compiled successfully`，`Route (app)` 表 11 个 app 路由（+`/_not-found`），未出现构建错误或告警升级。
- **为什么 86**：C 100 要求"首屏可交互 <2s"，R1 冷首载实测 `first-contentful-paint 3128ms`（单样本、跨境网络），该缺陷本轮既未修复也无法用一次新样本推翻（前端零改动）。C 85 档"三态基本完整、少数缺空态"已被超过（三态比 85 档更全），故 86 沿用。

### D · 安全与合规（15）→ **90**（**重判**，本轮核心变更）

**D 100 的五道面续核**（`loop/rounds/R1/scorecard.md:124-156` 的证据本轮仍成立）：

| 面 | 结论 | 证据 |
| --- | --- | --- |
| 凭据不落明文、不经 argv | ✅ | LLM Key Fernet 加密 + 只回末 4 位（`backend/services/llm_config_service.py:152`、`backend/tests/test_llm_config.py:35-39`）；仓库仅跟踪 `.env.example` |
| 敏感接口在访问控制清单内且默认拒绝 | ✅ | `backend/main.py:103-114`：`is_public = path == "/health" or path.startswith("/api/auth")`，其余 `/api/*` 一律要 Cookie，线上实测 401 `UNAUTHENTICATED` |
| 外部文本进模型前包裹为不可信内容 | ❌ **仍缺** | `git grep -n -E '忽略(上述|以下)|不可执行|外部内容|untrusted|<user_content>|delimiter' -- backend` → **0 命中**；`backend/core/role_prompts.py:189` 仅一句边界要求，无显式分隔/包裹 |
| 生成内容显式标识 | ✅ | `backend/domain/contracts.py` 的 `generated_by: Literal["model","fallback"]`；`backend/repositories/audit_repo.py:586-592` |
| 日志脱敏覆盖异常路径 | ✅ | `git grep -n -E 'logger\.(info\|warning\|error\|exception\|debug)[^\n]*(api_key\|token\|secret\|password)' -- backend` → 0 命中 |

**本轮修复点（应用侧，已核实成立）**：

1. `backend/core/config.py:26-56` `_parse_cors_origins`：逐个 origin 比较，`*` 直接丢弃并 `logging.warning("event=cors.wildcard_origin_ignored …")`（:42-49）；配置里只写 `*` 时回落到 `_DEFAULT_CORS_ORIGINS`（:13-18，localhost/127.0.0.1 3000/3001）+ `_KNOWN_PRODUCTION_ORIGINS`（:21，`https://www.brainstorming.top`、`https://brainstorming.top`），见 :54-55。
2. `backend/core/config.py:155-160` `@field_validator("cors_origins", mode="before")` 走同一解析（构造时传逗号分隔字符串也不会绕过）。
3. `backend/main.py:74-87` CORSMiddleware：`allow_origins=settings.cors_origins`、`allow_credentials=True`、`allow_headers=[Content-Type, Authorization, X-Requested-With, Idempotency-Key, Last-Event-ID, X-Request-ID]`，**没有配置 `expose_headers`**（这一点在 §5 是判定平台归属的关键）。
4. 回归测试（`backend/tests/test_platform_readiness.py:105` `class CorsPolicyTests`，4 例）：
   - `:108 test_parse_drops_wildcard_and_keeps_allowlist`：`"https://evil.example,*"` → `["https://evil.example"]`；
   - `:115 test_wildcard_only_falls_back_to_known_origins`：纯 `*` → 默认白名单 + 生产域；
   - `:123 test_unknown_origin_gets_no_cors_headers`：in-process `GET /health` + `Origin: https://evil.example` → 断言**响应不含** `access-control-allow-origin`（且业务响应仍是 200）；
   - `:128 test_allowlisted_origin_gets_cors_headers`：白名单 localhost:3001 → ACAO 回显。
   单跑结果：`Ran 16 tests in 0.223s / OK`，含上述 4 例全 `ok`，并可见 `cors.wildcard_origin_ignored` 告警。
5. **部署一致**：HF Space API 返回 sha `ce16bfa2c623d6ce18a48695d776b5a63e520930`、`lastModified 2026-11-26T15:55:40Z`、`runtime.stage RUNNING`；下载的 Space `backend/core/config.py` 与本地 HEAD **LF 归一化后 SHA256 完全相同（`63eb5309af668b86…`，5595 字符）** → 生产容器里跑的就是本轮修复后的代码。

**为什么从 85 调到 90**：

- R1 的 85 档依据是"存在 1 处单点绕过：生产 CORS 回显任意 Origin + `allow_credentials: true`"。该绕过在**应用层**已被关闭，且有可复跑证据（代码 + 4 例单测 + 部署代码一致 + in-process 断言无 ACAO）。因此"1 处单点绕过"不再成立于应用代码。
- 但 D 100 的"不可信内容包裹"这一面**仍然缺失**（0 命中），且生产链路上仍存在**非应用代码可移除**的凭据型 CORS 暴露（见 §5），故不能给 100。
- 落在 90（85 与 100 之间）：应用侧无绕过、无明文凭据落盘、无未覆盖的敏感接口；剩余是 1 个未实现面 + 1 个平台层风险。

### E · 产品逻辑与体验（15）→ **90**（沿用 R1，本轮复核无变化）

- 产品逻辑代码本轮零改动（`866aaf3..HEAD` 仅 `loop_sandbox.py` 属后端服务层，不在 7 步主链路路径上；前端零改动）。R1 的证据继续成立：`docs/full-journey-e2e.md:11-19` 七步 `succeeded` 记录（synthesis 39.6s/37509tk、总 tokens 53910）、`:39` 脚本内断言失败即非零退出、`:44` 复跑命令；断点兜底 `frontend/lib/api.ts:536-542`、`sse.ts:247-252`、`store/sessionStore.ts:448-483`、`components/system/ConnectionBanner.tsx`；文案不夸大（`LlmConfigGate.tsx:118-145`、`ops/page.tsx:107`）；空态可执行引导（`settings/access` 三区块、`ChatPanel.tsx:62`、`CanvasPanel.tsx:384`）；指标唯一定义 `usage_source ∈ provider|estimated`（`backend/core/llm_client.py:7,141-163`）。
- **为什么仍是 90**：E 100 要求"7 步每步都有完成判据与下一步入口"，本轮仍无法登录逐页实测（任务禁止登录），且 `docs/full-journey-e2e.md:31` vs `:38` 的口径矛盾仍在（该矛盾只在 G 维扣一次，但 E 维的"可验证闭环"证据因此仍不完整）。

### F · 测试与可验证性（10）→ **100**（**重判**）

锚点依据（rubric §2 F 100）：核心服务有直接测试 + 关键契约有断言 + 不依赖网络与真实站点 + 可并行 + **CI 存在且跑过** + 套件 <5min。逐条：

| 条件 | 结论 | 证据 |
| --- | --- | --- |
| 核心服务有直接测试 | ✅ | `test_contract_parity.py`、`test_schema_upgrade.py`、`test_platform_readiness.py`（本轮新增 16 例单跑 OK）、`test_runner.py`、`test_api_v2.py`、`test_command_service*` 等直接打服务/仓储层 |
| 关键契约（错误码/schema/幂等）有断言 | ✅ | 契约 parity **13** 例（R1 已核，本轮未改） |
| 不依赖网络与真实站点 | ✅ | 测试内 URL 均为占位（`example.com`）；CI 在无外网凭据的 ubuntu runner 上仍绿 |
| 可并行 | ✅ | CI 一次 run 内 `backend` / `frontend` / `browser-e2e` **三个 job 并行**执行，全部 success（45867837383） |
| CI 存在且**跑过** | ✅ | GitHub REST 复核：run `45867837383`（head `61454c9`）`conclusion=success`，created 2026-11-26T17:27:03Z；3 个 job 全 success，且**step 级逐条 success**：frontend（install/typecheck/unit tests/build/api smoke）、backend（install/unit+integration tests/import smoke/docs reference check `--strict`）、browser e2e（install×2/chrome version/start api/start web/wait/AC-01/AC-19/AC-31/upload screenshots）。上一条 run `45865717810`（head `eaa3e0e`）同样 3/3 success |
| 套件 <5min | ✅ | 本地 backend 73.8s/75.4s、frontend 334 例 ≈24s；CI job 耗时 backend **1.0 min**、frontend **1.4 min**、browser-e2e **1.1 min** |
| 每个永久 skip / 零测试核心模块 | 0 个 | 两套件均 `skipped 0`；未见零测试核心模块 |

**附加证据：CI 现在真的在守门（不是"配置存在"）**：全历史最近 6 跑中两次红灯（`45866788954` @`a8ad00f`、`45864289558` @`7513837`）的失败 step 精确为 `backend (python 3.12)` 的 **`docs reference check`**（单元测试与 import smoke 均成功），根因是文档引用体检把干净检出里不存在的 `.env/.next/data` 当断链；`61454c9`（"refs check 跳过生成物"）修掉后 HEAD 绿。这说明 CI 的失败**来自真实校验**而非环境噪声，也说明绿灯可信。第 43 批记录的 6 个根因（`docs/loop-runs/README.md:279`，逐条在 `:284-:289`）与提交序列一一对应。

### G · 文档与交付一致性（5）→ **86**（**重判**，降 4）

实测基准：后端 **841**（两跑 OK）、前端 **334**（一致）、契约 parity **13**、构建 **11 个 app 路由**（`/_not-found` 另计，表内 12 行）。

扣分明细（rubric G 扣分锚点：每处虚假声明 −2）：

| # | 位置 | 问题 | 扣 |
| --- | --- | --- | ---: |
| 1 | `docs/v4-acceptance-report.md:28`、`:30`、`:167` | 写 837（`:30` 更写成 "Ran 837 tests / OK / exit 0"），实测 **841** | −2 |
| 2 | `docs/v4-release-checklist.md:4`、`:9` | 同一文件两处写 837，实测 841 | −2 |
| 3 | `docs/v4-release-notes.md:11`、`:101` | `Ran 837 tests` / "后端全量（837）"，实测 841 | −2 |
| 4 | `docs/loop-runs/README.md:329` | "当前基线：**后端 837 用例 OK**"，实测 841；且与**同文件** `:291` "本地后端 **841** 两跑 OK" 自相矛盾（新增矛盾） | −2 |
| 5 | `docs/full-journey-e2e.md:31` vs `:38` | `:31` 表格写"报告来源 `fallback`（要点 0 / 未决 5）"，`:38` 断言写"PASS … 报告 source=model"（旧矛盾未修） | −2 |
| 6 | `docs/browser-qa.md:114-115` vs `:178` | `:114` 写"**未进 CI**：CI 里只有 `npm test` + `test:e2e`"，`:178` 写"CI 里有一个独立 job 跑 AC-01/AC-19/AC-31"；本轮已实测该 job 真实存在且绿（旧矛盾未修，且现在更是硬错） | −2 |
| 7 | `docs/v4-release-notes.md:13` | "`npm run build` exit 0（**7 条路由**，含 `/session/[id]/audit` **8.11 kB**）"；本轮 `npm run build` 实测 11 个 app 路由（12 行含 `/_not-found`），audit 页 **12.9 kB**（新发现的过期数字） | −2 |

合计：100 − 14 = **86**。

**正确的部分（不扣）**：前端 334 多处一致（`docs/v4-acceptance-report.md:46,167`、`docs/v4-release-notes.md:12`、`docs/v4-release-checklist.md:4`）；契约 parity 13 一致；`docs/v4-release-checklist.md` 新增的「2026-11-27 起 CI 全绿（run `45865717810`…）」**属实**；`docs/loop-runs/README.md:291` 的"CI 全绿 + 本地后端 841 两跑 OK + 前端 334 两跑一致"**属实**；第 43 批 6 个根因记录属实；限制章节（无持久卷、沙箱非安全边界）写明。

**为什么是 86 而不是 88/90**：R1 因 3 处 825 扣 6 分得 90；本轮把 825 改成了 **837**（方向对，但比实测少 4 例，且改的地方更多：4 组 7 处），又新增同文件 841/837 矛盾、多出一个路由数过期 —— 虚假声明从 3 处变为 6 处（按"组"计 4 组 + 2 矛盾 + 1 路由数 = 7 个 −2 项，扣 14）。仍未达到"数字与事实一致"。

> 敏感性：若复核者认为 release-notes:11 的结果表格属"历史记录"、不计入，则 G=88，总分 88.40（四舍五入 88）—— **结论不变**。

### H · 可运维性（5）→ **92**（沿用 R1，本轮复核无变化）

锚点依据（rubric §2 H 100）：启动前检查 + `/health` 区分存活/就绪并给 `blocking_reasons` + 日志带关联 id + 迁移可重复 + 失败退出码正确。8 个面中 7 个满足（R1 证据 `loop/rounds/R1/scorecard.md:214-227` 本轮仍成立：`backend/main.py:504/520/527` 三端点、`:486-500` 就绪判定、`:443-458` 真 `SELECT 1`、`repositories/database.py:773/797-862` + `test_schema_upgrade.py:99` 幂等、`scripts/container_start.py:160` 退出码、`scripts/pre_release_check.py` 前置检查；线上 `/health`、`/health/live`、`/health/ready` 本轮复跑均 200 且 `blocking_reasons: []`）。

**扣分面（本轮复测仍未修）**：日志行完全不含 request_id —— `git grep -n request_id -- backend` 共 **262** 命中，但 `| Select-String logger` → **0**；线上响应头/错误体有 `X-Request-Id`（本轮 `www` 401 响应头 `X-Request-Id: EdEH8V`），日志里没有 → 排障仍无法把日志与请求对齐（P1-2）。H=92 沿用。

---

## 4. 本轮"已修复/已发生"声称项 — 独立核实表

| # | 声称 | 独立核实 | 结论 |
| --- | --- | --- | --- |
| 1 | 应用侧 CORS 加固：`_parse_cors_origins` 丢弃 `*`、纯通配回落白名单 + 生产域；`CorsPolicyTests` 4 例在库 | 代码 `backend/core/config.py:26-56`/`:155-160`；`backend/main.py:74-87`；测试 `backend/tests/test_platform_readiness.py:105-133` 4 例全 `ok`；单跑 16 例 OK | **属实**（应用侧） |
| 2 | 生产仍回显 ACAO + ACAC + `expose-headers: *`，且反射来自 HF/Vercel 平台代理 | 4 条 curl 原文见 §5；决定性证据是 HF Space `GET /`（uvicorn 404）也带 `access-control-expose-headers: *` 与 `access-control-allow-credentials: true`，而应用 `main.py:74-87` **没有** `expose_headers` 配置 | **属实**（归因成立） |
| 3 | 部署代码 = origin/main 的 `backend/core/config.py` | Space `config.py` 与本地 LF 归一化 SHA256 **完全一致**（`63eb5309af668b86…`，5595 字符）；Space sha `ce16bfa2…`，lastModified 2026-11-26T15:55:40Z，RUNNING | **属实** |
| 4 | CI run `45867837383`（head `61454c9`）三 job 全 success；`45865717810`（head `eaa3e0e`）同样全 success；6 根因记录在 `docs/loop-runs/README.md` 第 43 批 | REST 复核 run/job/step 三级全 success；第 43 批在 `:279`，根因逐条 `:284-:289` | **属实** |
| 5 | 后端应为 841 用例、前端 334 | 后端 841 ×2 跑 OK；前端 334 pass；`tsc.cmd --noEmit` 0 | **属实（实测）**；但**文档仍写 837**（4 组 7 处）→ 声称"文档计数与实际一致"**不成立**（见 G） |
| 6 | P2：`backend/runner-test-*` 临时目录已删除 | 实测仍有 **12 个**目录（全空、合计 0.0 MB；R1 为 164 个 / 62.1 MB）；mtime 2026-11-26 23:53–2026-11-27 01:04，**均早于本轮测试开跑（01:34）** → 本轮两跑未新增 | **部分属实**（数量与体积大降、测试不再持续堆积，但未清零，且 `backend/.tmp_tests` 由测试引导正常重建） |

---

## 5. D 维专项：平台归因成立后，应用侧还剩什么真实风险

### 5.1 平台注入的证据链（本轮 01:50 重跑，可复跑）

```powershell
# [1] HF Space 直连预检：回显任意 Origin
curl.exe -D - -o NUL -X OPTIONS "https://123chenjunliang-pm-brainstorm-workbench.hf.space/api/v2/sessions" `
  -H "Origin: https://evil.example" -H "Access-Control-Request-Method: POST"
# → HTTP/1.1 200；access-control-allow-origin: https://evil.example；access-control-max-age: 600
#    vary: origin, access-control-request-method, access-control-request-headers

# [2] 生产域名预检（Vercel → HF）：同样回显
curl.exe -D - -o NUL -X OPTIONS "https://www.brainstorming.top/backend/api/v2/sessions" `
  -H "Origin: https://evil.example" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type"
# → 200；Access-Control-Allow-Origin: https://evil.example；Allow-Headers: content-type；Server: Vercel
#   Strict-Transport-Security: max-age=63072000

# [3] null Origin 也回显
curl.exe -D - -o NUL -X OPTIONS "https://www.brainstorming.top/backend/api/v2/sessions" -H "Origin: null"
# → 200；Access-Control-Allow-Origin: null

# [4] 决定性：应用自己的 404 响应也被平台加凭据头（应用中间件不可能产生 expose-headers:*）
curl.exe -D - -o NUL "https://123chenjunliang-pm-brainstorm-workbench.hf.space/" -H "Origin: https://evil.example"
# → HTTP/1.1 404；Content-Type: application/json；server: uvicorn
#   access-control-allow-credentials: true
#   access-control-expose-headers: *
#   access-control-allow-origin: https://evil.example
#   x-proxied-replica: ypud2rkf-4pqml / x-proxied-path: / / x-request-id: y4848q
#   link: <https://huggingface.co/spaces/...>;rel="canonical"

# [5] 应用根本不存在的路由，预检仍被平台 200 应答
curl.exe -D - -o NUL -X OPTIONS "https://123chenjunliang-pm-brainstorm-workbench.hf.space/api/v2/definitely-not-a-route" `
  -H "Origin: https://evil.example"
# → 200 + access-control-allow-origin: https://evil.example（应用对不存在路径应为 404/405）

# [6] 生产 API 的 401 响应同样被注入
curl.exe -D - -o NUL "https://www.brainstorming.top/backend/api/v2/sessions" -H "Origin: https://evil.example"
# → 401；Access-Control-Allow-Origin: https://evil.example；Access-Control-Expose-Headers: *
#   X-Proxied-Path: /api/v2/sessions；X-Proxied-Replica: ypud2rkf-4pqml；X-Request-Id: EdEH8V
```

判定：**反射不是应用中间件行为**。三条独立理由：(a) 应用 `main.py:74-87` 未配置 `expose_headers`，而线上出现 `access-control-expose-headers: *`；(b) 应用白名单（`config.py:13-21`）不含 `evil.example`，且 in-process 测试断言未知 Origin 无 ACAO；(c) 平台头 `x-proxied-replica` / `x-proxied-path` / `link: …huggingface.co/spaces/…` 只可能由 HF 代理产生。叠加 `server: uvicorn` 的 404 也带这些头，可以排除"应用把 `*` 配回去"的可能（`*` 在 `_parse_cors_origins` 里已被丢弃，且应用也不会产生 `expose_headers`）。

> 不可直接观察项：生产容器里 `CORS_ORIGINS` 环境变量的实际值被平台头掩盖，无法单独读取；但即使配成 `*`，应用现在也会丢弃并回落白名单，因此应用侧不会主动给 `evil.example` 加 ACAO。

### 5.2 应用侧真实风险（按可利用性排序）

**风险 1｜同注册域子域可绕过 `SameSite=lax`（真实、有条件）**
`auth_cookie_samesite="lax"`（`backend/core/config.py:131`）、Cookie 为 host-only（`auth_cookie_domain=""`，`backend/core/config.py:132`；`auth_routes.py:99` `domain=None`）。lax 只拦截**跨站**请求；`www.brainstorming.top` 与 `evil.brainstorming.top` 属**同站**（site = `brainstorming.top`），因此从被攻陷/被接管/被停放的同注册域子域发起的跨源请求会**携带** Cookie，而平台层回显该子域 Origin 且 `Access-Control-Allow-Credentials: true` + `expose-headers: *` → 浏览器允许读取认证接口响应。
- 成立条件：攻击者需控制 `brainstorming.top` 下任一子域（子域接管、废弃 DNS 记录、第三方服务 CNAME）。当前 `www` 由 Vercel 提供，其它子域是否存在/是否可接管**未验证**。
- 若应用不依赖平台代理（自定义域直连容器/换网关注入策略），该路径随平台头一起消失。

**风险 2｜`SameSite` 若被改成 `none`（或跨站前端回归），立即变成完整绕过**
代码里 `samesite` 来自配置默认 `"lax"`，**没有单测锁定该默认值**（`git grep -n samesite -- backend/tests` 无断言）。一旦有人为"前后端分离"把 `AUTH_COOKIE_SAMESITE=none` 配上（还需 `secure=True`），平台层已有的"回显任意 Origin + ACAC true"就直接变成可读数据的跨站绕过。建议加一条配置单测/启动校验（本项目历史上就出现过构建期 `NEXT_PUBLIC_API_URL=hf.space` 的跨域回归，见 `docs/loop-runs/README.md:329` 第 42 批描述）。

**风险 3｜公开的发码接口可被跨站触发（无凭据，仅滥用面）**
`POST /api/auth/email/code` 无需 Cookie（`backend/main.py:93` `is_public` 含 `/api/auth`），平台预检放行任意 Origin → 任意站点可让受害者浏览器以其 IP 触发发码。限流存在（`backend/core/auth.py:136-182`：每 IP 10 次/小时、每邮箱 5 次/小时、全局 30 次/分钟，B035），且**数据库不可用时降级放行并告警**（`:140`、`:185`）→ 滥用面为邮件轰炸/骚扰，不涉凭据泄露。

**风险 4｜其余凭据依赖面已收窄到只有 Cookie（本轮新增核实）**
- 前端 `git grep -n -E 'localStorage\.(set|get)Item' -- frontend` → **0**；`sessionStorage` 只用于登录冷却倒计时与访谈同意模式，不存 token。
- 前端 `git grep -n -E '"Authorization"|Authorization:' -- frontend/lib frontend/hooks` → **0**（没有任何 JS 侧凭据可被跨域读取）。
- 所有请求 `credentials: "include"`（`frontend/lib/api.ts:459,660`、`sse.ts:273,483`、`audio.ts:10`、`hooks/useSpeechRecognition.ts:56,144`）→ 凭据面唯一是 HttpOnly Cookie。
- 后端不解析 Bearer/Authorization（唯一命中是 `backend/main.py:81` 的 `allow_headers` 列表）；鉴权唯一入口 `backend/main.py:106` `verify_session_token(request.cookies.get(settings.auth_cookie_name))`。
- Cookie 属性：`httponly=True`、`secure=settings.auth_cookie_secure`（默认 **False**，`config.py:130`）、`samesite=lax`、host-only、`path="/"`（`auth_routes.py:92-101`）；登出 POST（`auth_routes.py:113`），GET 无副作用登出。
- 传输面缓解：`www` 返回 `Strict-Transport-Security: max-age=63072000`，`http://` → **308** 到 https（本轮实测）→ `secure=False` 的默认值在 HSTS 生效后风险有限，但仍是纵深防御缺口（P2-5）。
- 无 CSRF token（`git grep -n csrf -- backend` 仅注释命中）；缓解是 SameSite=lax + 写操作全 POST + 无 GET 变更操作（本轮只核到 `/logout` 为 POST，未穷举全部 GET）。

**结论**：`SameSite=lax` **足以阻断当前最主要的"跨站读数据"路径**（跨站 fetch/XHR 不带 Cookie，且无 JS token 可被当作替代凭据），但**不足以完全中和**平台层的凭据型 CORS 暴露：同注册域子域路径仍在，配置改成 `none` 即失效。因此 D 给 90（应用侧拦截已闭环，平台层作为部署风险挂 P1）；**若复核者坚持把平台反射计入产品安全责任，D 应记 85**，此时 D 闸门也不满足，**总体结论仍是 FAIL**（见 §11）。

---

## 6. P0 / P1 / P2 清单（本轮状态）

### P0（未关闭 → 封顶 89 + 判 FAIL）

**P0-1｜线上无持久卷：SQLite 落在容器内，平台重启/休眠回收 = 用户、会话、审计、加密模型密钥全部丢失且不可恢复**

- 状态：**未关闭（R2 仍未关闭）**。R1 之后只做了"披露"（health `warnings`），未挂持久卷、未换外部 DB。
- 本轮证据（线上只读，2026-11-27 01:44–01:50）：
  `curl.exe -s https://www.brainstorming.top/backend/health` →
  `{"status":"ok","warnings":["运行在 Hugging Face Space 且未设置 DATABASE_URL：SQLite 位于容器内，实例重启或休眠回收后数据会丢失（请挂持久卷或改用外部数据库）"]}`
  同一告警也出现在 Space 直连 `/health` 与 `www /backend/health/ready` 上 → 与 R1 文本逐字相同，说明配置未变。
- 代码位：`backend/main.py:461` `def _persistence_warnings() -> list[str]:`（`DATABASE_URL` 未设 + `SPACE_ID/SPACE_HOST` 存在 → 告警）→ `/health`（`main.py:505`）与 `/health/ready`（`main.py:527`）的 `warnings`（`:500`、`:513` 注入）；tests 覆盖 `backend/tests/test_platform_readiness.py:134` `PersistenceWarningTests`（3 例）。文档位 `README.md:53-57`、`docs/v4-release-checklist.md:150`（"HF 免费实例还有「重启丢 SQLite」与「封 SMTP」两个限制"）。
- 判断：rubric §3「已缓解/部分修复不算关闭」「数据损坏/丢失 + 环境限制不作为豁免理由」→ **未关闭**。
- 关闭判据（可复跑）：写入一条探针数据（会话/消息）→ 触发 Space 重启（或等平台回收）→ 读回仍在；且 `/backend/health/ready` 的 `warnings` 为空。
- 责任：**部署方/平台所有者**（用户）。应用代码侧只能告警，不能自建持久卷。

### P1

**P1-1｜生产 CORS 回显任意 Origin + `Access-Control-Allow-Credentials: true`（本轮重分类：应用侧关闭，平台侧未关闭）**

- 应用侧：**已关闭**（`backend/core/config.py:26-56` 丢弃 `*`、`:155-160` validator、`backend/main.py:74-87` 用白名单；`backend/tests/test_platform_readiness.py:105-133` 4 例，`:123` 断言未知 Origin 无 ACAO；deployed `config.py` 与 HEAD 逐字节一致）。
- 平台侧：**未关闭**（HF Space / Vercel 代理注入，见 §5.1 六条 curl；应用代码无法移除）。
- 关闭判据：① 部署层消除注入（自定义域直连容器、换网关、在 HF/Vercel 侧关闭 CORS 注入）；或 ② 若平台不可控 → 书面豁免 + 固化缓解（`SameSite=lax` 单测锁定、`auth_cookie_secure` 生产置 true、避免同注册域子域被接管、必要时把认证改为 `Authorization` + 不给凭据型 ACAO）。复跑：`curl -D - -X OPTIONS -H "Origin: https://evil.example" …` 期望不再出现 `Access-Control-Allow-Origin: https://evil.example`。
- 责任：**部署方/平台配置**（不是应用代码）。

**P1-2｜后端日志不含 request_id（排障无法把日志与请求对齐）**

- 状态：**未关闭**。`git grep -n request_id -- backend` → 262 命中，`| Select-String logger` → **0**；响应头有 `X-Request-Id`（本轮 www 401 实测 `X-Request-Id: EdEH8V`），日志行没有。
- 关闭判据：`git grep -n "request_id" backend | grep -c logger` > 0 且实际日志行含 request_id。
- 责任：**开发者**（后端日志格式）。

**P1-3｜CI 从未绿且不覆盖 HEAD**

- 状态：**已关闭 ✅**（run `45867837383`，head `61454c9`，`conclusion=success`，3/3 job + step 级全 success；run `45865717810`，head `eaa3e0e` 也 3/3 success）。历史两次红灯均为中间提交，失败 step 为 `docs reference check`，已由 `61454c9` 修掉。
- 关闭判据（已满足）：对当前 HEAD 触发 `ci.yml` 并全绿；REST 复核到 `conclusion=success`。

### P2（可挂账）

- **P2-1** `backend/runner-test-*`：**12 个空目录残留（0.0 MB；R1 为 164 个 / 62.1 MB）**；mtime 均早于本轮测试 → 本轮未新增，但未清零；`backend/.tmp_tests`、`.pytest_cache`、`.tmp`、`browser-e2e-98dhturl` 仍在。改判"部分改善"。
- **P2-2** 根目录 `.tmp_*`：已取消跟踪并 ignore（`git ls-files | Select-String '.tmp_'` → 0，commit `5015713`）；磁盘上是否仍有残留未核。
- **P2-3** 文档数字过期（G 维已扣）：837→841 共 4 组 7 处、同文件 841/837 矛盾、2 处旧矛盾、release-notes:13 的"7 条路由/8.11 kB"过期。
- **P2-4** 5 个 >1500 行文件（B 维结构性问题）：`command_service.py`(2619)、`runner.py`(2045)、`audit.py`(1886)、`contracts.py`(1812)、`sessionStore.ts`(2459)。
- **P2-5** `auth_cookie_secure` 默认 `False`（`config.py:130`）；生产实际值未验证；HSTS(2 年) + http→308 缓解。
- **P2-6** 冷首屏 FCP 3.1s（R1 单样本、跨境网络，本轮未复测）。
- **P2-7** 无 CSRF token（`git grep csrf -- backend` 仅注释）；缓解 SameSite=lax + 写操作 POST-only。

---

## 7. 残余风险（按概率×影响）

| # | 风险 | 等级 | 说明 |
| --- | --- | --- | --- |
| R-1 | 平台重启/回收 = 全量业务数据丢失 | **高（P0 未关闭）** | 唯一解是部署方挂持久卷/外部 DB；应用侧只能告警 |
| R-2 | 平台层凭据型 CORS（回显任意 Origin + ACAC true + expose-headers:*） | 中 | 应用侧已无法回显，但平台代理注入；同注册域子域路径可绕 SameSite=lax；`samesite` 改 `none` 即成完整绕过 |
| R-3 | 外部文本未包裹为不可信内容（提示注入面） | 中 | 0 命中；模型入口只有一句边界提示（`role_prompts.py:189`） |
| R-4 | 文档数字/口径与事实不符 | 中 | 会直接摧毁交付可信度（rubric G 常见误判） |
| R-5 | 日志无 request_id | 中 | 线上问题定位只能靠响应头对时间 |
| R-6 | 5 个巨型文件 | 中低 | 维护成本/合并冲突/单测难隔离 |
| R-7 | 冷首屏 >2s（跨境网络单样本） | 低 | 需多地区多设备样本 |
| R-8 | 发码接口可被跨站触发（无凭据） | 低 | 限流存在，但 DB 不可用时降级放行并告警 |

---

## 8. 无法验证项（明确声明，不假设通过）

1. **登录 / 验证码发送链路**：任务禁止登录、禁止发验证码、禁止调真实模型 → 无法验证生产 SMTP / `EMAIL_PROXY_URL` 是否可用；若都不可用则邮箱登录不可用（会构成新 P0）——本轮无证据，**不判 P0，也不判通过**。
2. **生产 Cookie 实际属性**：`SameSite`/`Secure`/`Domain` 实值需真实验证码换 Cookie 才能观察；平台是否改写 Cookie 未知。代码默认值可读（`config.py:129-132`），生产 env 覆盖值未知。
3. **生产 `CORS_ORIGINS` 实际值**：被平台注入头掩盖，无法单独读取；只能由"应用不会给非白名单加 ACAO + 部署代码一致"推断应用侧无绕过。
4. **平台 CORS 注入能否被部署层关闭**：需 HF/Vercel 控制台或自定义域直连，本轮无权限，未验证可行性。
5. **完整认证链路 API 冒烟（7 步主链路）**：禁止 POST 业务接口、禁止真实模型；只有 `docs/full-journey-e2e.md` 的 2026-11-24 历史记录。
6. **登录后页面（会话/审计/闭环/ops/团队）的运行时三态与"下一步入口"**：需登录。
7. **冷首屏 <2s**：仅 R1 的 1 次冷载样本（FCP 3.1s）+ 1 次 21s 网络超时；本轮未复测（前端零改动，沿用 R1 结论但不代表用户分布）。
8. **HF Space 后台配置**：持久卷、`DATABASE_URL`、`CORS_ORIGINS`、`AUTH_COOKIE_SECURE` 实值（只能由 `/health` warnings 反推 `DATABASE_URL` 未设）。
9. **CI runner 本地复现**：只能读 GitHub REST 结果（run/job/step），无法在本地复现 runner 环境；故 F 维按"CI 跑过且绿"计，未推测 runner 细节。
10. **`sessionStore.ts` 等行数口径**：`python splitlines` 2459 vs `Get-Content | Measure-Object -Line` 2237（后者跳过空行）；两口径均 >1500，不影响 B 结论，精确行数以 splitlines 为准（已与 R1 的 2619 等对齐）。

---

## 9. 可复跑命令合集

### 9.1 本地（只读，干净 HEAD）

```powershell
cd C:\Users\13067\Desktop\pm-brainstorming-workbench\backend
python -m unittest discover -s tests -t .          # 期望 Ran 841 tests / OK，两跑一致
python -m unittest tests.test_platform_readiness   # 期望 Ran 16 tests / OK（含 CorsPolicyTests 4 例）

cd ..\frontend
npm test                                            # 期望 tests 334 / pass 334 / fail 0 / skipped 0
.\node_modules\.bin\tsc.cmd --noEmit                # 期望 exit 0（不要用 npx tsc）
npm run build                                       # 期望 ✓ Compiled successfully，11 个 app 路由
```

```powershell
# B 维：>1500 行文件（权威口径）
cd C:\Users\13067\Desktop\pm-brainstorming-workbench
python -c "import pathlib
for f in ['backend/services/command_service.py','backend/workflows/runner.py','backend/workflows/audit.py','backend/domain/contracts.py','frontend/store/sessionStore.ts']:
    print(len(pathlib.Path(f).read_text(encoding='utf-8').splitlines()), f)"

# D 维：外部内容是否被包裹 / 日志是否泄露凭据
git grep -n -E '忽略(上述|以下)|不可执行|外部内容|untrusted|<user_content>|delimiter' -- backend   # 期望 0（当前缺陷点）
git grep -n -E 'logger\.(info|warning|error|exception|debug)[^\n]*(api_key|token|secret|password)' -- backend   # 期望 0

# H 维：日志是否带 request_id（期望 >0，当前 0）
git grep -n 'request_id' -- backend | Select-String -Pattern 'logger'

# G 维：过期数字
git grep -n '837' -- docs
git grep -n '841' -- docs
git grep -n '7 条路由' -- docs
```

### 9.2 线上只读探测（无需登录）

```powershell
# P0-1：持久卷告警（期望修复后 warnings 不含持久化告警）
curl.exe -s https://www.brainstorming.top/backend/health
curl.exe -s https://www.brainstorming.top/backend/health/ready

# P1-1：CORS 反射六条（期望修复后不再回显 evil.example / null）
curl.exe -D - -o NUL -X OPTIONS "https://123chenjunliang-pm-brainstorm-workbench.hf.space/api/v2/sessions" -H "Origin: https://evil.example" -H "Access-Control-Request-Method: POST"
curl.exe -D - -o NUL -X OPTIONS "https://www.brainstorming.top/backend/api/v2/sessions" -H "Origin: https://evil.example" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type"
curl.exe -D - -o NUL -X OPTIONS "https://www.brainstorming.top/backend/api/v2/sessions" -H "Origin: null"
curl.exe -D - -o NUL "https://123chenjunliang-pm-brainstorm-workbench.hf.space/" -H "Origin: https://evil.example"
curl.exe -D - -o NUL -X OPTIONS "https://123chenjunliang-pm-brainstorm-workbench.hf.space/api/v2/definitely-not-a-route" -H "Origin: https://evil.example"
curl.exe -D - -o NUL "https://www.brainstorming.top/backend/api/v2/sessions" -H "Origin: https://evil.example"

# D 维：部署代码一致性
curl.exe -s "https://huggingface.co/spaces/123chenjunliang/pm-brainstorm-workbench/raw/main/backend/core/config.py" -o "$env:TEMP\r2_space_config.py"
python -c "import pathlib,hashlib; a=pathlib.Path('backend/core/config.py').read_text(encoding='utf-8').replace('\r\n','\n'); b=pathlib.Path(r'$env:TEMP\r2_space_config.py').read_text(encoding='utf-8').replace('\r\n','\n'); print(hashlib.sha256(a.encode()).hexdigest()[:16], hashlib.sha256(b.encode()).hexdigest()[:16], a==b)"
```

### 9.3 CI 实况（只读 REST）

```powershell
$repo = "daniu-star/pm-brainstorming-workbench"
$runs = Invoke-RestMethod "https://api.github.com/repos/$repo/actions/runs?per_page=20" -Headers @{'User-Agent'='r2-eval'}
$runs.workflow_runs | Select-Object run_number, head_sha, conclusion, created_at | Format-Table
# 对 HEAD：期望 conclusion=success
Invoke-RestMethod "https://api.github.com/repos/$repo/actions/runs?head_sha=$(git rev-parse HEAD)" -Headers @{'User-Agent'='r2-eval'} | Select-Object total_count
Invoke-RestMethod "https://api.github.com/repos/$repo/actions/runs/45867837383/jobs" -Headers @{'User-Agent'='r2-eval'} | ForEach-Object { $_.jobs } | Select-Object name, conclusion
```

---

## 10. rubric §6 五问

**Q1 每一维为什么是这个分？（一句话锚点 + 证据位置）**

- A=95：841×2 全绿一致、0 skip、334 全绿；但 A 100 的"关键链路 API 冒烟 100%"未达成（§3 A）。
- B=70：命中 B 70 硬条件"存在 >1500 行文件"，5 个未拆（§3 B 表格，splitlines 口径）。
- C=86：无未转义拼接、三态完整；冷首屏 FCP 3.1s（§3 C）。
- D=90：应用侧 CORS 单点绕过已关闭（代码+4 测+部署一致+in-process 断言），平台层反射仍在、不可信包裹仍缺（§3 D、§5）。
- E=90：7 步有历史真模型记录 + 断点兜底；"每步下一步入口"未逐一实测（§3 E）。
- F=100：CI 在 HEAD 三 job step 级 success、套件 <5min、三套件并行、契约 13、无外网依赖（§3 F）。
- G=86：4 组 7 处 837（实测 841）+ 同文件新矛盾 + 2 旧矛盾 + 路由数过期，扣 14（§3 G 表）。
- H=92：8 面中 7 面满足；日志无 request_id（§3 H）。

**Q2 上一轮差距项这轮关了吗？**

| R1 差距 | 状态 | 证据 |
| --- | --- | --- |
| P0-1 无持久卷 | **未关闭** | 线上 `/health` warnings 逐字不变（§6 P0-1） |
| P1-1 CORS 回显任意 Origin | **应用侧已关闭 / 平台侧未关闭** | `config.py:26-56` + 4 测 + 部署一致；平台注入见 §5.1 |
| P1-2 日志无 request_id | **未关闭** | logger 0 命中（§3 H） |
| P1-3 CI 从未绿 | **已关闭 ✅** | run `45867837383` success（§3 F） |
| G 过期用例数 825 / 2 矛盾 | **未关闭（改了一半）** | 825→837 但实测 841，且新增矛盾（§3 G） |
| B 5 个 >1500 行文件 | **未关闭（0 拆分）** | splitlines 2619/2045/1886/1812/2459（§3 B） |
| P2 runner-test-* 164 个/62.1MB | **部分改善** | 12 个/0.0MB，本轮测试未新增（§6 P2-1） |

**Q3 有没有"声称成立但实际不成立"的说法？**

| 声称 | 核实 | 结论 |
| --- | --- | --- |
| "CORS 已加固，线上不再回显任意 Origin" | 应用侧成立、平台侧不成立（§5.1） | **部分不成立**：应用代码已修，但生产响应仍回显 evil/null |
| "文档计数与实际一致（841/334）" | 841/334 实测正确，但文档 4 组 7 处仍写 837 | **不成立**（−14 已在 G 计入） |
| "P2 `runner-test-*` 已删除" | 12 个空目录仍在（0.0MB） | **部分不成立** |
| "CI 三 job 全 success" | REST 三级（run/job/step）复核全 success | **成立** |
| "部署 sha 与 origin/main 的 config.py 一致" | LF 归一化 SHA256 完全相同、Space RUNNING | **成立** |
| "第 43 批 6 个根因" | `docs/loop-runs/README.md:284-289` 与提交序列一一对应 | **成立** |

**Q4 硬闸门逐条？** 见 §1 表格：总分 88.30 < 95 ❌（差 6.70）；A 95 ✅；D 90 ✅（贴线）；P0 1 个 ❌；独立签字 ✅ → **FAIL**。

**Q5 历史口径一致性？** 与 R1（85.95，HEAD `866aaf3`）口径一致：同 rubric V1.0、同权重、同扣分系数；R1 的 A/B/C/E/H 结论在本轮"窗口快照"上复核无变化（B 行数用 splitlines 复测与 R1 完全一致），D/F/G 因本轮修复重判并逐条写明依据。与 `loop/metrics/R1.json` 的 proxy（98.8，残缺输入）**不一致**——按 rubric §7，proxy 只作输入锚点，本卡以人工实测为准，两者并列时以本卡为准（R1 §Q5 已解释同一问题）。历史分数可直接比较（未改口径），R1→R2 = **+2.35**。

---

## 11. 距 PASS 还差什么（不凑分、不改权重）

**需要 +6.70 加权分（等价 Σ(分×权重) +670）。** 按"单项可得"排序：

| 杠杆 | 现分→目标 | 可得 | 谁来做 | 备注 |
| --- | --- | ---: | --- | --- |
| B 拆 5 个 >1500 行文件 | 70 → 85 | **+225** | 开发者（后端/前端） | `command_service.py`(2619)/`runner.py`(2045)/`audit.py`(1886)/`contracts.py`(1812)/`sessionStore.ts`(2459) 拆到 <1500（最优 ≤800） |
| C 冷首屏 <2s + 三态逐页 | 86 → 95 | **+135** | 前端 | 首屏资源/CDN/本地基准；R1 单样本 3.1s |
| A 完整认证链路冒烟 | 95 → 100 | **+100** | 开发者 + 评测授权 | 需不依赖真实模型的 e2e harness（mock LLM）或授权真实模型 |
| E 7 步下一步入口逐一实测 | 90 → 95 | **+75** | 开发者 + 可登录评测环境 | 需登录后逐页走查 |
| D 不可信内容包裹 + 平台 CORS 处置 | 90 → 95 | **+75** | 开发者 + 部署方 | `role_prompts.py`/`context_builder.py` 增加显式不可信包裹；平台层关闭注入或书面豁免 |
| G 文档数字与事实一致 | 86 → 95+ | **+45** | 开发者/文档 | 841、路由数、两处旧矛盾、同文件矛盾 |
| H 日志带 request_id | 92 → 100 | **+40** | 开发者 | 日志格式加 request_id，并做一次线上取证 |
| **合计** | — | **+695** | — | ≥670，但要求上述 7 项**几乎全做** |

**关键提醒**：① **P0-1 必须先关闭**（部署方挂持久卷或配外部 `DATABASE_URL`），否则即使加权分 ≥95 也会被 rubric §3 封顶到 89；② 只做文档/日志类小项（G+H+E）最多 +160，总分仅 ~90 以内，**不可能达标**；B 与 C 是最大杠杆；③ A=100 受任务环境限制，本轮无法靠评测组单方面完成，需要修复组提供可复跑的 mock-LLM 全链路 e2e。

**责任分配**：P0-1、P1-1 平台侧、持久卷/`DATABASE_URL`/平台 CORS → **用户/部署方**；B/C/E/D/H/G/A 的代码与文档、mock e2e → **修复组（开发者）**；A 维的真实链路复跑授权 → **Loop 主导方/评测授权**。

---

## 12. 独立复核签字

- 评测人：`score-r2`（root=`lead` 下的独立评测子代理）
- 身份声明：**非修复作者**——`866aaf3..HEAD` 的 12 个提交均非本代理产出；本代理**只写入了本文件**，未改产品代码、未 commit、未登录站点、未发验证码、未调用真实模型。
- 写入范围：`git status --porcelain` 收尾时只有 `?? loop/rounds/R2/`（即本卡）；取证中途出现的并发队友未跟踪 PNG 已由对方处理，非本代理创建也未删除。
- 评分环境：Windows / PowerShell 7；仓库 HEAD `61454c9`；`tsc` 使用 `frontend\node_modules\.bin\tsc.cmd`（未安装任何包）。
- 复跑留痕（本地日志）：
  - 后端两跑：`%TEMP%\r2_backend_tests.txt`（Ran 841 / 73.847s / OK）、`%TEMP%\r2_backend_tests2.txt`（Ran 841 / 75.401s / OK / exit 0）
  - 前端：`%TEMP%\r2_frontend_tests.txt`（334/334 pass / 0 fail / 0 skip / exit 0）
  - Space 部署 `config.py` 下载快照：`%TEMP%\r2_space_config.py`（与本地 LF 归一化 SHA256 相同）
  - CI：run `45867837383`（head `61454c9`，3/3 success）、`45865717810`（head `eaa3e0e`，3/3 success）；红 run `45866788954`（`a8ad00f`）、`45864289558`（`7513837`，失败 step = `docs reference check`）
- 线上观测时间：2026-11-27T01:44–01:50 +08:00（GMT 17:44–17:50），只读 GET/OPTIONS/HEAD，无业务写操作。
- 复核结论：**FAIL**，总分 **88.30**（四舍五入 **88**），未关闭 P0 **1** 个（P0-1 持久化）。
- 签字时间：2026-11-27T01:56+08:00。
