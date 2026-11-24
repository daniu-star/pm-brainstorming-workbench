# 审计报告真实模型路径验收（第 17 批）

本文件由 `scripts/audit_report_e2e.py` 生成：在**真实模型**上跑「澄清 → brief 确认 → 审计开场 → 作答 → 结束出报告」，验证报告来自模型（`generated_by=model`）而不是确定性兜底。

- 运行时间：2026-11-24 12:13:58
- 模型：`deepseek-flash`
- 关闭思考：`True`
- 总耗时：11.0s

## 1. 分步

| Run | 状态 | 耗时 | tokens | 调用次数 |
| --- | --- | --- | --- | --- |
| clarify | succeeded | 1.8s | 1693 | 2 |
| start_audit | succeeded | 1.9s | 1693 | 2 |
| answer_audit | succeeded | 2.5s | 2045 | 2 |
| end_audit | succeeded | 4.5s | 2647 | 2 |

## 2. 报告

| 指标 | 值 |
| --- | --- |
| 审计状态 | `completed` |
| 问题数 | 2 |
| `generated_by` | **`model`** |
| 报告修订数 | 1（最后一条 `model`） |
| supported_points | 0 条 |
| unresolved_gaps | 9 条 |
| recommended_experiments | 4 条 |
| summary 字数 | 0 |
| 最终阶段 | `audit_completed` |

## 3. 断言

```text
PASS 报告由模型生成（generated_by=model）
PASS 报告至少有一条 supported_points 或 unresolved_gaps
PASS 审计 completed 且阶段 audit_completed
PASS 报告修订表里有对应记录
```

## 4. 复跑

```bash
cd backend && ../.venv/Scripts/python.exe ../scripts/audit_report_e2e.py
```

> 成本：一次约 0.8–1.5 万 tokens，不要放进 CI。
