# Loop Engineering Product

> 把「产品从想法到可交付」做成一条可复现的流水线：**规划 → 研发 → 测试 → 迭代 → 评测**，
> 由 7 个虚拟角色组成一支研发团队，每轮真跑测试、独立打分，5 轮或 95 分收敛。

这是一个适用于 AI Agent（Claude Code / Codex / DSH / 任意支持 workflow 编排的运行时）的 **skill**：
它不是一个提示词，而是一套**组织流程 + 评分卡 + 可执行脚本**。

## 为什么需要它

大多数「AI 帮我做产品」的失败方式，不是模型不会写代码，而是：

| 现象 | 后果 |
|---|---|
| 只审计不真跑 | 报告里的「已修复」是推演出来的 |
| 角色不隔离 | 前端顺手改后端，越改越乱 |
| 打分没有硬上限 | 存在 P0 也能拿 90 分 |
| 没有收敛条件 | 无限循环，或草草收场 |
| 修复不可验证 | 改完没人复跑，缺陷原地复活 |

Loop Engineering Product 用**固定工艺**把这些问题钉死：角色写权限硬隔离、评分硬上限、
每轮必须有真实命令输出、退出条件明确。

## 五段闭环

```
┌──────────────────────────── 每一轮 ────────────────────────────┐
│                                                                │
│  PLAN ──▶ AUDIT ──▶ SCORE ──▶ FIX ──▶ RETEST ──▶ 下一轮        │
│  产品组    5 组并行   评测组   双端并行  测试组                  │
│  定口径    只读审计   独立打分  最小改动  真跑命令               │
│                                                                │
└────────────────────────────────────────────────────────────────┘
        score ≥ target 或 轮次耗尽  ──▶  DELIVER（逐轮报告 + 未闭合项）
```

- **PLAN**：把需求收敛成本轮目标、验收口径、非目标、风险（防范围蔓延）。
- **AUDIT**：产品组 / 设计体验组 / 客户端组 / 服务端组 / 测试组 **并行只读审计**。
- **SCORE**：评测组先真跑一条命令自证，再按 8 维评分卡打分，执行硬上限。
- **FIX**：客户端 + 服务端并行修复，测试组同步补回归用例，写权限硬隔离。
- **RETEST**：测试组真跑全量（后端 + 前端类型检查/单测/构建/e2e），真实数字即证据。

## 团队编制

| 角色 | 关注点 | 写权限 |
|---|---|---|
| 产品组 | 需求闭环、主流程、范围蔓延 | `docs/**`（规格增量） |
| 设计体验组 | 空/加载/失败/超时/无权限/冲突状态、可恢复性 | 只读 |
| 客户端开发组 | 竞态、丢更新、去重、版本单调、SSE | `frontend/**` |
| 服务端开发组 | 幂等、状态机、租约、事务、越权、错误码 | `backend/**` |
| 测试组 | 真跑、覆盖缺口、跨进程/崩溃注入、flake | `backend/tests/**`、`frontend/tests/**` |
| 评测组 | 独立打分、硬上限裁定、僵局判断 | 只读 |
| 发布运维组 | CI、启动脚本、健康检查、迁移回滚、文档 | `scripts/**`、`.github/**`、`docs/**` |

## 8 维评分卡（100 分）

```
产品与需求达成 20 | 功能正确性 15 | 数据一致性与状态机 15 | 交互与前端逻辑 15
架构与代码质量 10 | 安全与错误处理 10 | 自动化测试与证据 10 | 文档与可运维 5
```

**硬上限**：存在未修 P0 → ≤70；≥3 条未修 P1 → ≤85；测试被削弱 → ≤60 且必须回退。

## 快速开始

```js
// 用 workflow 工具执行（DSH / 兼容 runtime）
workflow({
  meta: { name: "loop-engineering-product", description: "五段闭环产品研发 Loop" },
  args: {
    repoPath: "/abs/path/to/your/repo",
    maxRounds: 5,
    target: 95,
    focus: "",
    backendTestCmd: "cd backend && python -m unittest discover -s tests",
    frontendCmds: ["npx tsc --noEmit", "npm test", "npm run build"]
  },
  script: /* skills/loop-engineering-product/scripts/loop-workflow.js 的内容 */,
  run_in_background: true
})
```

产物：每轮 `docs/loop-runs/round-N.md`，汇总 `docs/loop-runs/README.md`（模板见 `assets/report-template.md`）。

## 目录结构

```
loop-engineering-product/
├── SKILL.md                      # skill 入口（何时用 + 流程 + 评分卡）
├── references/
│   ├── harness.md                # Harness 10 条契约（schema/gate/scope/证据/回滚…）
│   ├── roles.md                  # 7 个角色的职责书与禁止事项
│   ├── rubric.md                 # 评分锚点与硬上限裁定流程
│   └── protocol.md               # 轮次协议、发现项格式、证据纪律、僵局处理
├── scripts/
│   ├── loop-workflow.js          # 可直接执行的闭环脚本
│   └── verify_write_scopes.py    # H3 写权限隔离校验（越界即 FAIL）
└── assets/
    └── report-template.md        # 逐轮 / 汇总报告模板
```

## 设计原则

1. **证据优先**：没有命令输出的「已修复」视为未修复。
2. **写权限隔离**：一个角色只碰自己那一侧，跨侧需求走 findings。
3. **最小改动**：一轮只解最高 ROI 的缺陷，拒绝顺手重构。
4. **硬上限**：评分不是感觉，是规则；有 P0 就不可能 90 分。
5. **可回退**：每轮改动都有验证命令，失败可回滚并记录。
6. **诚实交卷**：达不到阈值就说达不到，输出未闭合项而不是漂亮话。

## License

MIT © 2026 Loop Engineering Product contributors