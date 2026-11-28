# R4 评分卡（Loop 第 4 轮）— 独立评测组

- 评分人：`score-r4b`（独立评测组子代理，**不是**本轮任何修复的作者；本轮只读代码 + 只读命令 + 只读网络探测，未改产品代码、未 commit/push、未改 HF Space、未登录站点、未发验证码、未建会话、未调用模型 API、未发邮件；唯一写入是本文件）
- **被评提交（scored target）= `9af8e95`（`9af8e95223c782fac70b18a84ddea573e8ea86fb`）**，`git rev-parse HEAD origin/main` 两者相同 → 已推送至 `origin/main`，CI 在该提交上跑过（见 §3-F）
- 评分基准：`loop/rubric.md`（V1.0，227 行，冻结未改）；权重 A20 / B15 / C15 / D15 / E15 / F10 / G5 / H5；总分 = Σ(分×权重)/100（四舍五入到整数，**闸门用未取整值**）
- 评测时间：2026-11-28 19:00–19:15 +08（线上只读探测 19:02 +08）
- 线上状态：前端 `/`、`/login`、`/settings/llm`、`/ops` 均 **200**；`/backend/health` 200（带持久化 warnings）；`/backend/health/ready` 200，`persistent=true`、`persistence.kind=snapshot`、`durable=true`、`interval_seconds=120`、`worst_case_loss_seconds=120`；`/backend/api/v2/sessions`（无 cookie）**401 `UNAUTHENTICATED`**，错误体含 `request_id` 且与响应头 `X-Request-Id` 一致
- 本轮范围：按任务只做 6 件事（读 rubric/历史卡、后端全量、文档与部署自检、行数统计、线上只读探测、3 处代码抽查）；未额外扩大
- **可否作为发布依据：否（FAIL）** —— 总分 88.75 未达 95，且 P0-1 未按 rubric 口径关闭

---

## 1. 8 维打分表

| 维度 | 权重 | 分数 | 加权分 | 关键证据（文件:行号 / 命令输出） | 扣分理由 |
| --- | ---: | ---: | ---: | --- | --- |
| **A 功能正确性与回归稳定性** | 20 | **95** | 19.00 | 后端全量一次：`Ran 913 tests in 133.002s` / `OK` / `EXIT=0` / `skipped` 命中 0；文档数字亦为 913（`docs/v4-release-notes.md:11`、`docs/v4-acceptance-report.md:30`） | A=100 要求「同一命令两跑一致」+「关键链路 API 冒烟 100%」。本轮按任务只跑一次，且无法跑认证链路（禁登录/禁模型）→ 停在 95（与 R3 同口径） |
| **B 服务端工程质量** | 15 | **70** | 10.50 | `wc -l`：`backend/services/command_service.py` 2619、`frontend/store/sessionStore.ts` 2459、`backend/workflows/runner.py` 2044、`backend/workflows/audit.py` 1885、`backend/domain/contracts.py` 1811、`frontend/app/session/[id]/loop/page.tsx` 1704、`backend/tests/test_audit_workflow.py` 1568 → 与 R3 **逐项相同**，零拆分 | rubric B 70 硬条件「存在 >1500 行文件」持续命中（7 个，其中 5 个为 R3 点名的主文件）。新增 `db_snapshot.py` 383 行质量尚可，但重构/新脚本按 rubric 不加分 |
| **C 前端与交互质量** | 15 | **86** | 12.90 | `git diff --stat eeee88f..HEAD -- frontend` 为空 → **前端产品代码自 R3 起零改动**；沿用 R2/R3 证据（无未转义 `innerHTML`、三态/aria 大体完整、冷首屏 FCP 3128ms > 2s） | 首屏 >2s 未改善；少数视图缺空态；本轮无浏览器/CDP 复测（无法核实 console error 与 FCP），不抬分 |
| **D 安全与合规** | 15 | **90** | 13.50 | 应用侧 CORS 白名单 + `allow_credentials=True` 未回退（R2 关闭项）；线上 401 错误体/响应头一致（`X-Request-Id: IZ2X4B` = 体 `request_id`）；快照令牌只从环境变量读取（`backend/core/db_snapshot.py:87-120`），日志只打 repo/字节数、不打 token（`:290,359`）；异常体截断 200 字符（`:364`） | 维持 R3 的 90：「外部文本进模型前包裹为不可信内容」仍缺（`backend/core/role_prompts.py:189` 仅一句边界提示）；本轮新增「整库（含加密模型 Key）推到私有 HF dataset」的外传面，但私有 repo + Space secret，未判为扣分；平台层 ACAO 反射本轮未带 Origin 复测 |
| **E 产品逻辑与体验** | 15 | **90** | 13.50 | 本轮 diff 无产品/前端业务代码（`git diff --stat eeee88f..HEAD` 仅 backend 持久化 + scripts + docs + 测试）；沿用 R2/R3 的 7 步闭环/断点兜底证据 | 「每步下一步入口」仍未逐页实测 → 不给 100；无新增缺陷 → 不降（与 R3 同） |
| **F 测试与可验证性** | 10 | **100** | 10.00 | GitHub CI run **`45948015897`**，`head_sha=9af8e95223c782fac70b18a84ddea573e8ea86fb`（= 被评提交），`conclusion=success`；套件 133.0s < 5min；新增 `backend/tests/test_db_snapshot.py`（26 个 test 函数）用 mock urlopen，不依赖外网 | F 100 六条（核心服务有直接测试、契约断言、不依赖网络、可并行、CI 跑过、<5min）均满足；唯一小瑕疵（测试结束 `OK` 后仍泄漏子进程 `--list` 输出）属卫生问题，不足以降档，记为 P2 |
| **G 文档与交付一致性** | 5 | **90** | 4.50 | 已修：874→913（`docs/v4-acceptance-report.md:30`、`docs/v4-release-notes.md:11,101`、`docs/v4-release-checklist.md:4,9`）；`verify_docs_refs.py --strict` → 26 文件 0 断链 | 仍有 4 处不实/矛盾（每处 −2，理论 92，保守取 90）：① `docs/v4-acceptance-report.md:28` 注释仍写「841 用例」而 `:30` 写 913；② `docs/loop-runs/README.md:367` 仍写体检「8 步」而 `:302` 写 9 步；③ `docs/v4-release-checklist.md:119` 称 request_id「前端提示里会带出来」，但 `grep -rn requestId frontend/lib frontend/components frontend/app` 仅命中 `frontend/lib/api.ts`，components/app 0 命中 → 假；④ `docs/loop-runs/README.md:283` 写 `test_db_snapshot.py` 25 例，实测 26 个 test 函数 |
| **H 可运维性** | 5 | **97** | 4.85 | 启动前检查：`scripts/container_start.py:37-52` 先 `restore_snapshot` 后 `initialize_database`；health 区分 live/ready 且 ready 给 `blocking_reasons` / `degraded_reasons` / `persistent` / `persistence`；日志带 `[rid=]`（R3 已关，本轮线上 401 头体一致复证）；迁移幂等沿用 `backend/repositories/database.py:773,797-862`；GBK 崩溃已修（`PYTHONIOENCODING=gbk` 下实跑 EXIT=0、输出 `[OK]`） | 扣 3：`persistent`/`durable` 由**静态配置**推断（`backend/main.py:557,578-585`），上传失败计数 `SnapshotUploader.failures` 未暴露给任何健康/运维接口（`backend/core/db_snapshot.py:321-372`）→ 快照链路早已失效时 ready 仍会报 `durable=true`（潜在「健康检查撒谎」面） |
| **合计** | **100** | — | **88.75** | — | **FAIL** |

**加权计算**：`(95×20 + 70×15 + 86×15 + 90×15 + 90×15 + 100×10 + 90×5 + 97×5) / 100 = (1900 + 1050 + 1290 + 1350 + 1350 + 1000 + 450 + 485) / 100 = 8875 / 100 = 88.75`

---

## 2. 总分与硬闸门逐条（rubric §4）

| 闸门 | 要求 | 实测 | 结论 |
| --- | --- | --- | --- |
| 1 总分 | ≥95（未取整） | **88.75** | ❌ 差 **6.25** |
| 2 A | ≥95 | **95** | ✅ 满足（贴线） |
| 3 D | ≥90 | **90** | ✅ 满足（贴线） |
| 4 未关闭 P0 | 0 个 | **1 个**（P0-1，见 §4） | ❌ 不满足 |
| 5 独立复核签字 | 非修复作者 | `score-r4b` 独立评测组 | ⚠️ **本轮不签字**（仅 A/D 过、总分与 P0 未过；rubric 要求四项全过才可 PASS） |

**结论：FAIL（2 条闸门不满足：总分、P0 未清零）。**
封顶规则：命中未关闭 P0 → 总分封顶 89；本轮 88.75 < 89，**封顶不额外生效**（若后续加权分 >89 仍会被压到 89）。

---

## 3. 与 R3 的逐维对比（R3 = 88.50，A95 B70 C86 D90 E90 F100 G85 H97）

| 维度 | R3 | **R4** | Δ | 变化依据 |
| --- | ---: | ---: | ---: | --- |
| A 功能正确性 | 95 | **95** | 0 | 913 全绿、0 skip；仍缺两跑一致与认证链路冒烟 → 同 R3 |
| B 服务端工程 | 70 | **70** | 0 | 7 个 >1500 行文件与 R3 逐项相同（`wc -l` 实测），零拆分 |
| C 前端交互 | 86 | **86** | 0 | 前端产品代码自 R3 起零 diff；未复测浏览器，不抬不降 |
| D 安全合规 | 90 | **90** | 0 | 应用侧加固未回退；「不可信内容包裹」仍缺；平台层未复测 |
| E 产品闭环 | 90 | **90** | 0 | 产品逻辑零改动 |
| F 测试可验证 | 100 | **100** | 0 | CI run 45948015897 在**被评提交本身**上 success（R3 是前一提交），套件 133s |
| G 文档一致性 | 85 | **90** | **+5** | R3 的最大扣分项（874 vs 878，8 处/4 文件）已全部改 913；但仍有 3 处旧矛盾 + 1 处新数字矛盾，故只回补 5 分 |
| H 可运维性 | 97 | **97** | 0 | 新增「先恢复后建表」+ 持久化模式字段 + GBK 修复；但 durable 只看配置、上传失败不可见，仍不给 100 |
| **合计** | 88.50 | **88.75** | **+0.25** | **仍 FAIL** |

**一句话判读**：本批把 R3 明确点名的两件事真正做掉了 ——（1）P0-1 从「重启全量丢失」降为「有界丢失 / 可恢复」并落到代码与线上字段；（2）部署自检的 Windows GBK 崩溃与 874 数字错误已修（H/G 各自回补）。但**唯一能大幅抬分的 B 维（7 个大文件）零动作，C/E/D 也零动作**，所以总分只 +0.25，离 95 还差 6.25；P0 按 rubric「部分修复不算关闭」仍判未关闭。

---

## 4. P0 专项判定：**P0-1 显著缓解，但按 rubric 仍判未关闭（降级为有界丢失）**

事实（全部可复跑）：

1. 线上 `/backend/health/ready` → 200，`persistence = {"kind":"snapshot","durable":true,"repo":"123chenjunliang/pm-brainstorm-db","interval_seconds":120,"worst_case_loss_seconds":120}`，`persistent=true`；`warnings`/`degraded_reasons` 自述「最坏会丢失最近 120 秒内的写入」。
2. 代码链路成立（三处抽查全部名副其实）：
   - `backend/core/db_snapshot.py:202-220` `copy_sqlite_consistent` 确用 `src_conn.backup(dst_conn)`（sqlite3 online backup API），非 `shutil.copy`；
   - `:153-156` `encode_snapshot = base64.b64encode(gzip.compress(raw, 6))` → gzip+base64；
   - `:228-262` `restore_snapshot` 全程 `try/except` 只返回字符串，docstring 明写「恢复失败不能阻止服务启动」→ **不上抛**；
   - `scripts/container_start.py:37-52` 的 `_prepare_database` 先 `restore_snapshot(snapshot_config())`、后 `initialize_database()`，docstring 明写「顺序很关键：先恢复、后建表」；
   - `backend/main.py:557` `"persistent": bool(persistence.get("durable"))`，且 `:578-585` snapshot 模式 `durable=True`。
3. 线上配置确实启用（`persistence.kind=snapshot` 只能来自 `DB_SNAPSHOT_REPO` + token 均已配置）。
4. 残余风险：最坏丢 120s 写入（硬杀/非优雅退出）；且**关闭 P0 所需的「重启后数据仍在」证据只有作者文档**：`docs/loop-runs/README.md:293` 写「线上 `RESTART46 OK 5/5`：Space 重启后同账号仍可登录、会话 `99b3cde8ff144eb88e2102ac04c5fd52` 仍在」。评测组**不能登录、不能重启 Space**，无法独立复跑该证据。

按 rubric §3 判：P0 定义含「数据损坏」，关闭判据必须是**可复跑证据**，且明确「已缓解/部分修复不算关闭」。本轮把故障从「全量不可恢复」改为「有界丢失、可恢复」，是实质改进，但仍不满足关闭判据 → **P0-1 判未关闭（严重性由「全量丢失」降为「最坏 120s 丢失」）**。这不影响本轮 FAIL（总分本身 88.75 < 95）。

---

## 5. 新发现问题清单（P0/P1/P2）

### P0（无新增；沿用未关闭 P0-1）
- **P0-1 线上持久化：已缓解但未按 rubric 关闭** —— 证据与判据见 §4；残余：最坏 120s 写入丢失；重启生存证据不可独立复跑（`docs/loop-runs/README.md:293`）。

### P1（用户可感知/可靠性）
- **P1-1 快照链路失败对健康检查与监控不可见**：`/health/ready` 的 `persistent`/`durable` 由静态配置推断（`backend/main.py:557` + `:578-585`），而 `SnapshotUploader.failures` / `uploads` 计数没有任何出口（`backend/core/db_snapshot.py:321-372` 只 `logger.warning`）。复跑：`grep -rn "failures" backend/main.py backend/api` 0 命中。后果：token 过期/仓库写满/网络长期失败时，`ready` 仍报 `durable:true`，运维无法据此报警。
- **P1-2 快照相关关键声明无法独立验证（评测组标记，不抬高也不据此扣分）**：`docs/loop-runs/README.md:283` 的「真实 dataset 往返 643072 字节 / sessions:35 / messages:46 / integrity:ok」与 `:293` 的「RESTART46 OK 5/5」均只有作者文档；本机无 token、不能登录、不能重启 Space，无法复跑。

### P2（局部质量，可挂账）
- **P2-1 快照历史无压缩（数据集仓库线性增长）**：`backend/core/db_snapshot.py:149-150` 定义了 `snapshot_squash_url(repo)`（HF super-squash），但全仓 grep `super-squash|snapshot_squash_url` 仅命中定义处 → **从未调用**。每次内容变化都整文件 commit（线上体积约 101KB 文本/次），长期积累会拖慢推送、逼近仓库配额。
- **P2-2 文档数字矛盾（同文件相邻）**：`docs/v4-acceptance-report.md:28` 命令注释仍写「后端全量（841 用例…）」，`:30` 已是 `Ran 913 tests`。复跑：`grep -n "841" docs/v4-acceptance-report.md`。
- **P2-3 体检步数矛盾**：`docs/loop-runs/README.md:367` 当前基线写「`PRE_RELEASE_OK`（8 步，含 db-report）」，而同文件 `:302` 写「体检 9 步」，`scripts/pre_release_check.py:43-53` 实为 9 步。
- **P2-4 request_id 前端展示声明不实**：`docs/v4-release-checklist.md:119` 称「错误体 `request_id`（前端提示里会带出来）」，实测 `grep -rn requestId frontend/lib frontend/components frontend/app` 只命中 `frontend/lib/api.ts:132,142,151,426,438…`（仅存入 `ApiError`），**components/app 0 命中**。
- **P2-5 测试输出泄漏（测试卫生）**：后端全量在 `Ran 913 tests / OK` **之后**仍打印 `pre_release_check --list` 步骤名（docs/deploy-env/…/db-report），说明 `backend/tests/test_pre_release_check.py:42-43` 的 `test_list_prints_all_steps` 里子进程 stdout 未捕获，会污染 CI 日志与「最后几行」判定。复跑同 §1-A 命令即可见。
- **P2-6 恢复条件过窄**：`backend/core/db_snapshot.py:242-243` 仅当本地库不存在或 size==0 时才恢复；本地库存在但损坏（半写/手工改坏）时不会回退到远端快照。
- **P2-7 文档测试计数偏差**：`docs/loop-runs/README.md:283` 写「`test_db_snapshot.py` 25 例」，`grep -c "def test_"` 实测 26。
- **P2-8 沿用未修（R2/R3 已记）**：`auth_cookie_secure` 默认 False（`backend/core/config.py:130`）；无 CSRF token（SameSite=lax 缓解）；7 个 >1500 行文件（§1-B）。

---

## 6. 无法验证项（明确声明，不假设通过、不据此抬分）

| 项 | 原因 | 本卡处理 |
| --- | --- | --- |
| 线上「重启后数据仍在」5/5（RESTART46） | 评测组禁止登录站点、禁止重启/改 HF Space，无法建会话再跨重启读取 | 只作为作者声明记录（§4/§5），不据此关闭 P0 |
| 私有 dataset 真实往返与字节数 | 仓库私有、无 HF token；下载 `resolve/main/app.db.gz.b64` 需授权 | 未验证，不抬分 |
| CI step 级是否全 success | 时间预算内只取了 run 级（`conclusion=success`，head 精确匹配），未拉 job/step 明细 | 以 run 级为准，判 F=100 |
| 前端 console error / 首屏 FCP / 三态截图 | 本轮无浏览器与 CDP 驱动，且任务未要求前端套件 | 沿用 R3/R2 证据，C 保守 86 |
| 真实登录→BYOK→建会话→Run 认证链路 | 禁止发验证码、禁止调用模型 | A 停在 95 |
| 平台层 ACAO 反射 / ACAC | 本轮线上探测未带 `Origin` 头（未复跑 R3 的六条 curl） | D 维持 90，不据此加减 |
| `/backend/health/live` 单端点 | 任务只要求 `/health` 与 `/health/ready`（两者均 200） | 未单独验证，不影响判定 |
| 前端 334 用例 / `tsc` / build | 任务未要求，且 20 分钟预算优先保后端与安全项 | 记为未跑 |
| 其余 601 条文档引用的数字正确性 | 只跑了 `verify_docs_refs.py --strict`（断链 0），未逐条核对数字 | G 保守 90 |

---

## 7. 距 PASS 的差距与杠杆（供下一轮参考）

差额：`95 − 88.75 = 6.25` 加权分，且需先按 rubric 口径关闭/或正式豁免 P0-1。

| 杠杆 | 动作 | 加权收益 | 验收条件（可复跑） |
| --- | --- | ---: | --- |
| 0. P0-1 | 独立复跑重启演练并留证据（建会话→重启→带 Cookie 读回）；或改外部 Postgres；或正式书面接受 120s 窗口并改 rubric 口径 | 0（解除 89 封顶） | 重启前后数据对比 JSON + `/health/ready` 字段；评测组可复跑 |
| 1. B（最大杠杆） | 拆 7 个 >1500 行文件至 ≤800 行 | **+3.00（B90）～+4.50（B100）** | `wc -l` 全部 ≤800 且 913 例 + 契约 13 例全绿、CI 绿 |
| 2. C | 首屏可交互 <2s（真机 5 次中位数）+ 补空态 + console 0 error | +0.90（C92） | CDP 采样脚本断言 |
| 3. A | 认证链路冒烟脚本化（stub 模型）+ 同命令两跑一致 | +1.00（A100） | 一条命令 `SMOKE_OK n/n` |
| 4. G | 修 841/8 步/request_id 三处 + 补 26 例计数 | +0.50（G100） | `grep -n "841" docs/v4-acceptance-report.md` 为空等 |
| 5. D | 外部文本进模型前结构化包裹 | +0.30（D92） | 新增测试断言 + 实现 grep |
| 6. H | 把 `SnapshotUploader.failures` 暴露到 `/health/ready` 或 `/health/detail` | +0.15（H100） | 断网模拟后 ready 显示上传失败计数 |

组合：0 + 1(B93≈+3.45) + 2 + 3 + 4 + 6 ≈ **+6.10 → 94.85**，仍差 0.15；即 **B 必须拆到接近 100（或同时拿 C/E 增量）才可能过 95**。

---

## 8. 声明

本卡仅依据可复跑命令与 `文件:行号`；所有分数均可追溯。评测组与修复作者无重叠；本轮**不签字**（4 项闸门仅 2 项满足）。评测期间未修改仓库任何其他文件。
