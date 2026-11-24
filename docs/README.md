# 文档索引

本目录按「用途」而不是「写作时间」组织。部署前读第 1 组，改代码读第 2 组，
复跑真实链路读第 3 组，追溯某次修复读第 4 组。

## 1. 发布与验收（上线前必读）

| 文档 | 内容 | 何时读 |
| --- | --- | --- |
| `v4-release-notes.md` | 一页纸发版说明：能力全景、42 条 AC 分组、代表修复、已知限制 | 对外说明「这个版本做了什么」 |
| `v4-release-checklist.md` | 部署顺序、回滚步骤、验收命令清单 | 每次发版 / 换机器部署 |
| `v4-acceptance-report.md` | AC-01…AC-42 逐条判据、证据（测试/脚本/截图）、已知偏差 | 评审验收是否达标 |

## 2. 架构与接口

| 文档 | 内容 | 何时读 |
| --- | --- | --- |
| `v4-m1-interfaces.md` | 领域契约、仓储/服务/SSE 语义、状态机转移、幂等约定 | 改后端任何一层之前 |
| `loop-studio-interfaces.md` | Loop Studio 的接口、文件归属、与主流程的边界 | 改 Loop Studio |
| `v5-loop-studio.md` | Loop Studio 的能力说明与使用方式（设想→检验→开发→测试→评测） | 用闭环工作台做产品验证 |
| `superpowers/specs/2026-11-18-pm-brainstorm-agent-architecture-prd-v4.md` | PRD V4.0 原文（需求基线，AC 编号来源） | 判断「原始需求到底是什么」 |

## 3. 运行与真实链路证据

这些文档是**脚本跑出来的**，不是手写承诺；每条都带复跑命令。

| 文档 | 内容 | 何时读 |
| --- | --- | --- |
| `browser-qa.md` | 浏览器交互验收协议 + CDP 自动化 e2e（AC-01/19/31/33） | 改前端交互 / 验收页面行为 |
| `browser-qa/e2e/` | 上述 e2e 的 7 张归档截图 | 需要证据截图时 |
| `full-journey-e2e.md` | 完整用户旅程真模型记录（clarify→brief→四角色→审计，约 5.4 万 tokens） | 怀疑「端到端还能不能跑通」 |
| `audit-report-e2e.md` | 结项报告真模型路径（断言 `generated_by=model`、摘要非空） | 改报告生成 / 模型适配 |
| `ac20-number-provenance.md` | 每轮脑暴论断里数字的来源与「未验证」标注 | 检查数字可追溯性 |
| `ac20-number-provenance.annotation.md` | 上述结果的人工核对结论（100% 标注、无编造引用） | 需要人工标注证据时 |

## 4. 逐批记录（热修历史）

每批修复都记录「发现 → 根因 → 修法 → 回归证据 → 提交号」，用于避免重复踩坑。

| 文档 | 内容 | 何时读 |
| --- | --- | --- |
| `loop-runs/README.md` | **逐批热修总表 + 当前基线**（后端/前端用例数、e2e、回滚演练） | 想知道「现在是什么状态」先看这里 |
| `loop-runs/product-loop-2026-11-19.md` | 多角色 Loop（7 角色 / 8 维评分）逐轮运行记录 | 追溯某轮评分与整改 |
| `qa-loop-engineering.md` | 早期 QA Loop 方法学（7 维评分、门禁、留痕） | 了解评测机制演进 |
| `qa-loop-runs/round-1.md` … `round-5.md` | 早期 QA Loop 的 5 轮归档 | 追溯早期发现 |

## 5. 历史审计材料

`audit/report.md`、`audit/buglist.md`、`audit/checklist.md` 是 PRD 实施期的审计快照，
结论已被后续批次取代，仅作历史参考。

## 维护约定

- **新增文档**：放进对应分组并在本索引登记一行（脚本不强制，但 review 会看）。
- **文档引用检查**：`python scripts/verify_docs_refs.py --strict`（CI 已接入）。
  它校验 Markdown 链接与反引号里的仓库路径是否真实存在。
- **提到已删除的文件**：用删除线标注（`~~backend/db/session_store.py~~`），
  脚本会跳过删除线段；确属历史叙述的已删除模块也在脚本的 `REMOVED_TARGETS` 白名单里。
- **数字口径**：用例数、tokens、分数一律以本目录下脚本/报告的输出为准，
  不要在多个文档里手写不同的数字——先跑命令，再回填。
