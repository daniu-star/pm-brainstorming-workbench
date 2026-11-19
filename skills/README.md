# Skills

可复用的 Agent 工程能力包。每个目录是一个自包含 skill：入口是 `SKILL.md`，参考材料在 `references/`，可执行脚本在 `scripts/`，模板在 `assets/`。

| Skill | 用途 | 入口 |
|---|---|---|
| `loop-engineering-product` | 五段闭环产品研发 Loop：规划 → 研发 → 测试 → 迭代 → 评测，7 角色团队，5 轮或 95 分收敛 | [SKILL.md](./loop-engineering-product/SKILL.md) |

## 约定

- 每个 skill 自带 `LICENSE`（默认 MIT）；
- `SKILL.md` 的 frontmatter 至少包含 `name`、`description`、`version`、`license`；
- 脚本必须可直接运行，且不依赖私有凭据；
- 报告与产物写进使用方的 `docs/`，不写回 skill 目录。