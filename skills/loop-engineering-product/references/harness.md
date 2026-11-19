# Harness 契约（Loop Engineering Product v1.0）

Harness = 承载 Loop 的运行时（DSH workflow、Claude Code 子代理、CI 脚本……）。
Loop 的健壮性不取决于模型多聪明，而取决于 Harness 是否强制了下面这些约束。
落地任何 Loop 前，先逐条确认你的 Harness 能做到。

## H1. 结构化产出（schema 强制）

- 每个角色调用的返回必须落进 JSON Schema（`FINDINGS/SCORE/FIX/RETEST/PLAN`）。
- Schema 用 `additionalProperties: false`，缺字段直接判失败并重试一次。
- 禁止用「自然语言段落」当评分结果：分数、P0/P1 数量必须是数字字段，可被代码门禁读取。

## H2. 阶段门禁（gate）

```
score.total >= target AND score.p0 == 0  → DELIVER
否则                                     → 下一轮
```

- gate 必须由脚本判断，不能由 agent 自己宣布「可以交付了」。
- 轮次上限、目标分数通过参数注入（`maxRounds` / `target`），不写死在 prompt 里。

## H3. 写权限隔离（write scope）

- 每个修复角色在 prompt 里被授予且仅被授予一侧路径（`frontend/**` 或 `backend/**`）。
- Harness 侧建议再加一道：修复结束后 `git diff --name-only` 校验改动路径是否越界，越界则回滚该角色改动。
- 测试角色只改 `tests/**`；文档角色只改 `docs/**`、`scripts/**`、`.github/**`。

## H4. 证据强制（evidence）

- 评测组的 `verdict` 必须包含至少一条真实命令输出片段（正则校验：`Ran \d+ tests|exit 0|pass \d+|OK`）。
- 测试组必须给出真实数字；「跑不动」写进 `failures`，不得静默降级为通过。
- 每轮产物落盘：`docs/loop-runs/round-N.md`，含发现、评分、改动、复测输出。

## H5. 修复可回滚

- 每轮开始前记录 `git stash create` 或 `git rev-parse HEAD` 作为基线。
- 一轮结束后若复测劣化，回滚该轮改动并在报告中记录「回滚原因」。
- 不支持 git 时，至少让修复角色输出 `filesChanged` 并在复测失败时人工介入。

## H6. 预算与超时

- 单轮 agent 调用数上限（推荐 9~11）、单 agent 超时（推荐 20 分钟）。
- 上游模型调用预算由业务侧（如 `MAX_UPSTREAM_CALLS_PER_RUN`）控制，Loop 不额外放大。
- 预算耗尽时**显式失败**并保留已完成阶段，不要静默减配。

## H7. 幂等与可恢复

- 同一轮重复执行不产生重复发现/重复修复（发现 id 稳定：`<角色>-<序号>`）。
- 长时 Loop 支持断点续跑：`rounds` 状态可序列化，恢复后从下一轮开始。
- 修复角色失败不阻塞其它角色；单角色失败记 `skipped` 并在评测中扣分。

## H8. 可观测

- 每个阶段 `phase()` 可被外部轮询（便于长时任务监控）。
- 结束时返回结构化结果：`{reachedTarget, finalScore, stopRound, rounds[], openItems[]}`。
- 进度日志写真实分数（`Round N: 82/100（P0=1 P1=2）`），不写「进展顺利」。

## H9. 并发与竞争

- 审计阶段并行（互不写），修复阶段并行但写域互斥。
- 若两个修复角色可能碰同一文件 → 串行化，或前置 `pipeline` 保证顺序。
- 评测必须在修复与复测**之后**执行，避免「边改边评」。

## H10. 诚实性检查（anti-false-green）

评测组与 Harness 双重检查：

| 检查 | 方式 |
|---|---|
| 删用例 | `git diff --stat tests/` 出现删除行 |
| 改期望值 | 测试文件中断言行减少 |
| skip/xit | 新增 `skip`/`xit`/`test.todo` |
| 恒真断言 | 新增 `assertTrue(True)` 类 |
| 只测字符串 | 新增断言只做 `in` 判断关键路径 |

命中任一项 → 该轮分数上限 60，且要求回退。Harness 无法自动判定时，至少把 diff 证据交给评测组复核。

## 最小可用 Harness 清单

- [ ] 能并行调用多个 agent 并收集结构化结果
- [ ] 能注入 schema 并校验返回
- [ ] 能按阶段打点、可被外部监控
- [ ] 能读 git 状态、能记录 baseline
- [ ] 能把每轮报告写入磁盘
- [ ] 有轮次上限与目标分数门禁