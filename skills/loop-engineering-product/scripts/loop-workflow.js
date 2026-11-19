// Loop Engineering Product —— 五段闭环产品研发 Loop（由 DSH workflow 工具加载执行）
//
// 用法：把本文件内容作为 workflow 工具的 `script` 传入；参数：
//   args = {
//     repoPath:  "<绝对仓库路径>",
//     maxRounds: 5,            // 轮次上限
//     target:    95,           // 收敛分数
//     focus:     "",           // 本轮额外关注（可选）
//     backendTestCmd:  "cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests",
//     frontendCmds:    ["npx tsc --noEmit", "npm test", "npm run build"]
//   }
//
// 流程：PLAN（产品组收敛本轮口径）→ AUDIT（5 组并行只读审计）→ SCORE（评测组独立打分 + 硬上限）
//      → [未达标] FIX（客户端/服务端并行 + 测试组补回归）→ RETEST（测试组真跑）→ 下一轮
// 退出：分数达标或轮次耗尽；返回逐轮明细 + 未闭合项 + 发布建议。

const REPO = String(args.repoPath || ".");
const MAX_ROUNDS = Number(args.maxRounds || 5);
const TARGET = Number(args.target || 95);
const FOCUS = String(args.focus || "");
const BACKEND_TEST_CMD = String(
  args.backendTestCmd || "cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests"
);
const FRONTEND_CMDS = Array.isArray(args.frontendCmds) && args.frontendCmds.length
  ? args.frontendCmds
  : ["npx tsc --noEmit", "npm test", "npm run build"];

// ---------------------------------------------------------------- schemas

const PLAN = {
  type: "object",
  additionalProperties: false,
  required: ["goal", "acceptance", "nonGoals", "risks"],
  properties: {
    goal: { type: "string" },
    acceptance: { type: "array", items: { type: "string" } },
    nonGoals: { type: "array", items: { type: "string" } },
    risks: { type: "array", items: { type: "string" } },
  },
};

const FINDINGS = {
  type: "object",
  additionalProperties: false,
  required: ["group", "findings"],
  properties: {
    group: { type: "string" },
    findings: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["id", "area", "severity", "title", "evidence", "impact", "fix", "verify"],
        properties: {
          id: { type: "string" },
          area: { type: "string" },
          severity: { type: "string", enum: ["P0", "P1", "P2"] },
          title: { type: "string" },
          evidence: { type: "string" },
          impact: { type: "string" },
          fix: { type: "string" },
          verify: { type: "string" },
        },
      },
    },
  },
};

const SCORE = {
  type: "object",
  additionalProperties: false,
  required: ["total", "verdict", "dimensions", "fixes", "p0", "p1"],
  properties: {
    total: { type: "number" },
    verdict: { type: "string" },
    dims: { type: "array", items: { type: "object", additionalProperties: true } },
    dimensions: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["name", "score", "max", "reason"],
        properties: {
          name: { type: "string" },
          score: { type: "number" },
          max: { type: "number" },
          reason: { type: "string" },
        },
      },
    },
    fixes: { type: "array", items: { type: "string" } },
    p0: { type: "number" },
    p1: { type: "number" },
    releaseVerdict: { type: "string" },
  },
};

const FIX = {
  type: "object",
  additionalProperties: false,
  required: ["scope", "filesChanged", "changes", "commands", "result"],
  properties: {
    scope: { type: "string", enum: ["frontend", "backend", "tests", "docs"] },
    filesChanged: { type: "array", items: { type: "string" } },
    changes: { type: "array", items: { type: "string" } },
    commands: { type: "array", items: { type: "string" } },
    result: { type: "string" },
    skipped: { type: "array", items: { type: "string" } },
    risk: { type: "string" },
  },
};

const RETEST = {
  type: "object",
  additionalProperties: false,
  required: ["backend", "frontend", "failures"],
  properties: {
    backend: { type: "string" },
    frontend: { type: "string" },
    failures: { type: "array", items: { type: "string" } },
    flaky: { type: "array", items: { type: "string" } },
    gaps: { type: "array", items: { type: "string" } },
  },
};

// ---------------------------------------------------------------- prompts

const COMMON = `
仓库：${REPO}
你是「Loop Engineering Product」虚拟研发团队的一员，按固定工艺协作。
红线（违反即视为无效产出）：
- 只信真实证据：结论必须能对应到 文件:行号 / 接口请求 / 命令输出；推演与"应该没问题"不算证据。
- 不削弱测试：禁止删用例、改期望值、skip、恒真断言来换取绿色。
- 不越界改动：只在你被授予的写权限范围内改动；跨侧需求写进 findings 交给对应角色。
- 环境受限时如实记录（网络不可达、端口占用、依赖缺失），不得用"已验证"掩盖。
- 不改 git 历史、不执行 push、不新增依赖（除非本轮明确要求）。
${FOCUS ? "- 本轮额外关注：" + FOCUS : ""}
`;

function planPrompt(round) {
  return `${COMMON}
你是【产品组 · 规划阶段】。这是第 ${round} 轮。
先读仓库里的产品文档（README、docs/ 下的 PRD/验收矩阵/发布清单/架构说明），然后给出本轮口径：
- goal：本轮要达成的用户价值（一句话，可验证）；
- acceptance：3~6 条验收口径（用户动作 → 期望结果）；
- nonGoals：本轮明确不做的事（防范围蔓延）；
- risks：最可能让本轮失败的 3 个风险（技术/流程/环境）。
只读，不改代码。`;
}

function auditPrompt(title, extra) {
  return `${COMMON}
你是【${title}】。只读审计当前代码与文档，输出结构化发现。
要求：
- 最多 10 条，按严重度排序；每条含 文件:行号 证据、用户可见影响、最小修法、可执行验证方式；
- 不重复已修复项，除非能证明它仍然是缺陷（给出复现）；
- 宁缺勿滥：没有证据的猜测不要写。
${extra}`;
}

function scorePrompt(round, auditJson, retestJson, plan) {
  return `${COMMON}
你是【评测组】。第 ${round} 轮独立打分。
第一步必须自证：真跑至少一条验证命令，并把你看到的真实输出写进 verdict：
- 后端：${BACKEND_TEST_CMD}
- 前端（任选）：${FRONTEND_CMDS.join(" ; ")}
第二步按 8 维评分卡打分（合计 100）：
产品与需求达成 20 / 功能正确性 15 / 数据一致性与状态机 15 / 交互与前端逻辑 15 /
架构与代码质量 10 / 安全与错误处理 10 / 自动化测试与证据 10 / 文档与可运维 5。
硬上限：存在未修 P0 → total ≤ 70；存在 ≥3 条未修 P1 → total ≤ 85；发现测试被削弱 → total ≤ 60 并要求回退。
输出：total、verdict（含真实命令输出片段）、dimensions（8 项，逐项 reason 写清扣分点）、fixes（下一轮最小改动集，按 ROI 排序）、p0、p1、releaseVerdict。
本轮计划：${JSON.stringify(plan).slice(0, 2000)}
上一轮复测：${retestJson ? JSON.stringify(retestJson).slice(0, 3000) : "（无）"}
本轮审计：${JSON.stringify(auditJson).slice(0, 14000)}`;
}

function fixPrompt(scope, scoreJson, auditJson) {
  const sideMap = {
    frontend: "客户端开发组，只改 frontend/**（页面/组件/store/lib）",
    backend: "服务端开发组，只改 backend/**（领域/仓储/服务/工作流/API）",
    tests: "测试组，只改 backend/tests/** 与 frontend/tests/**（补回归用例）",
    docs: "发布运维组，只改 docs/**、scripts/**、.github/**（文档/脚本/CI）",
  };
  return `${COMMON}
你是【${sideMap[scope]}】。
只按评测组清单 + 审计发现动手，最小改动：
- 每处改动给出验证（命令或新增用例名）；
- 不改他人写权限内的文件；不新增依赖；不削弱测试；
- 修不了的写进 skipped 并说明原因，不要假装修好。
评测清单：${JSON.stringify(scoreJson).slice(0, 9000)}
审计发现：${JSON.stringify(auditJson).slice(0, 12000)}`;
}

function retestPrompt(round) {
  return `${COMMON}
你是【测试组 · 复测】。第 ${round} 轮。
必须真跑并逐条引用真实输出（数字、exit code、失败用例名）：
1) ${BACKEND_TEST_CMD}
2) 前端逐条：${FRONTEND_CMDS.join(" ; ")}
跑不动的如实写进 failures；偶发失败写进 flaky；覆盖缺口写进 gaps。不要用推演替代真实输出。`;
}

// ---------------------------------------------------------------- run

phase("plan");
log(`Loop Engineering Product 启动：repo=${REPO} maxRounds=${MAX_ROUNDS} target=${TARGET}`);
const plan = await agent(planPrompt(1), { schema: PLAN, label: "产品组 · 规划", phase: "plan" });
log(`本轮目标：${plan ? plan.goal : "（规划失败，按仓库现状默认口径）"}`);

const rounds = [];
let finalScore = 0;
let reachedTarget = false;
let stopRound = 0;
let lastRetest = null;

for (let round = 1; round <= MAX_ROUNDS; round += 1) {
  stopRound = round;

  phase(`round-${round}-audit`);
  const audits = await parallel([
    () =>
      agent(
        auditPrompt(
          "产品组 · 验收审计",
          "关注：主流程端到端可走通性、需求/AC 覆盖、入口可发现性、空态与失败恢复、范围与用户价值。"
        ),
        { schema: FINDINGS, label: `R${round} 产品组`, phase: `round-${round}-audit` }
      ),
    () =>
      agent(
        auditPrompt(
          "设计体验组",
          "关注：每个界面的空/加载/成功/失败/超时/无权限/并发冲突状态，错误后能否重试与返回，文案与可访问性。"
        ),
        { schema: FINDINGS, label: `R${round} 设计体验组`, phase: `round-${round}-audit` }
      ),
    () =>
      agent(
        auditPrompt(
          "客户端开发组 · 审计",
          "关注：竞态与丢更新、去重 key、版本单调、订阅生命周期、错误分类（404/409/5xx）、SSE 解析与超时。"
        ),
        { schema: FINDINGS, label: `R${round} 客户端组`, phase: `round-${round}-audit` }
      ),
    () =>
      agent(
        auditPrompt(
          "服务端开发组 · 审计",
          "关注：幂等占位、状态机非法转移、租约与原子领取、事务边界、越权与软删、错误码语义、预算与超时。"
        ),
        { schema: FINDINGS, label: `R${round} 服务端组`, phase: `round-${round}-audit` }
      ),
    () =>
      agent(
        auditPrompt(
          "测试组 · 审计",
          "关注：覆盖缺口、跨进程/崩溃注入真实性、flake、缺失用例（给出可直接落地的用例名与断言）。"
        ),
        { schema: FINDINGS, label: `R${round} 测试组`, phase: `round-${round}-audit` }
      ),
  ]);
  const auditSet = audits.filter(Boolean);

  phase(`round-${round}-score`);
  const score = await agent(scorePrompt(round, auditSet, lastRetest, plan), {
    schema: SCORE,
    label: `R${round} 评测组`,
    phase: `round-${round}-score`,
  });
  if (!score) {
    log(`Round ${round}: 评测组未返回，终止并交卷`);
    break;
  }

  const record = {
    round,
    total: score.total,
    verdict: score.verdict,
    releaseVerdict: score.releaseVerdict || "",
    dimensions: score.dimensions,
    p0: score.p0,
    p1: score.p1,
    fixes: score.fixes,
    audits: auditSet,
    patches: [],
    retest: null,
  };
  rounds.push(record);
  finalScore = score.total;
  log(`Round ${round}: ${score.total}/100（P0=${score.p0} P1=${score.p1}）`);

  if (score.total >= TARGET && Number(score.p0 || 0) === 0) {
    reachedTarget = true;
    break;
  }
  if (round === MAX_ROUNDS) break;

  phase(`round-${round}-fix`);
  const patches = await parallel([
    () =>
      agent(fixPrompt("frontend", score, auditSet), {
        schema: FIX,
        label: `R${round} 客户端修复`,
        phase: `round-${round}-fix`,
      }),
    () =>
      agent(fixPrompt("backend", score, auditSet), {
        schema: FIX,
        label: `R${round} 服务端修复`,
        phase: `round-${round}-fix`,
      }),
    () =>
      agent(fixPrompt("tests", score, auditSet), {
        schema: FIX,
        label: `R${round} 测试补强`,
        phase: `round-${round}-fix`,
      }),
  ]);
  record.patches = patches.filter(Boolean);

  phase(`round-${round}-retest`);
  record.retest = await agent(retestPrompt(round), {
    schema: RETEST,
    label: `R${round} 复测`,
    phase: `round-${round}-retest`,
  });
  lastRetest = record.retest;
}

phase("deliver");
const openItems = [];
for (const r of rounds) {
  for (const a of r.audits || []) {
    for (const f of a.findings || []) {
      if (f.severity === "P0" || f.severity === "P1") {
        openItems.push({ round: r.round, severity: f.severity, area: f.area, title: f.title, evidence: f.evidence });
      }
    }
  }
}

return {
  reachedTarget,
  finalScore,
  stopRound,
  plan,
  rounds,
  openItems: openItems.slice(0, 60),
  summary: `Loop Engineering Product 结束：${stopRound} 轮，最终 ${finalScore}/100（目标 ${TARGET}）；${
    reachedTarget ? "达到阈值，可交付" : "未达阈值，需按未闭合项继续迭代"
  }`,
};