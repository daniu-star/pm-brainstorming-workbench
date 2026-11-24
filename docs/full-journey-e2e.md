# 完整用户旅程端到端（真实模型）

本文件由 `scripts/full_journey_e2e.py` 生成：在**真实模型**上把「提出问题 → 澄清 → brief 确认 → 四角色脑暴 → 审计开场 → 作答 → 收口报告」整条主链路跑一遍，并记录每一步的状态与 token 账本。

- 运行时间：2026-11-24 12:06:51
- 模型：`deepseek-flash`
- 预算档位：`standard`（关闭思考：`True`）

## 1. 分步结果

| 步骤 | 命令 | 动作 | Run 状态 | 耗时 | tokens |
| --- | --- | --- | --- | --- | --- |
| 1 | `create_session` | clarify | succeeded | 1.9s | 1827 |
| 2 | `revise_brief` | revise_brief | succeeded | 0s | 0 |
| 3 | `confirm_brief` | confirm_brief | succeeded | 0s | 0 |
| 4 | `brainstorm` | synthesis | succeeded | 39.6s | 37509 |
| 5 | `start_audit` | start_audit | succeeded | 2.7s | 2963 |
| 6 | `answer_audit` | answer_audit | succeeded | 2.3s | 3285 |
| 7 | `end_audit` | end_audit | succeeded | 7.1s | 8326 |

## 2. 最终产物

| 指标 | 值 |
| --- | --- |
| 会话阶段 | `audit_completed` |
| 会话版本 / 产品版本 | 13 / 6 |
| 消息数 | 14 |
| 论断 / 方案 / 决策 / 实验 / 未决 | 18 / 4 / 2 / 4 / 20 |
| 画布分支 | 技术、设计、商业、用户、方案、决策、未决、实验 |
| 未验证数字节点 | 5 |
| 审计 | `completed`，问题 2 个，已答 1 个，报告来源 `fallback`（要点 0 / 未决 5） |
| 本轮总 tokens | 53910 |

## 3. 断言（脚本内校验，失败即非零退出）

```text
PASS clarify 收口：run=succeeded，phase clarifying → clarifying
PASS 无残留活动 Run；审计 completed；报告 source=model；阶段 audit_completed
```

## 4. 复跑

```bash
cd backend && ../.venv/Scripts/python.exe ../scripts/full_journey_e2e.py
# 保留临时库现场（排查用）：JOURNEY_KEEP=1 ...
```

> 成本提示：一次完整旅程约 3.5–4 万 tokens（四角色脑暴占大头）。不要把它放进 CI，也不要反复跑。
