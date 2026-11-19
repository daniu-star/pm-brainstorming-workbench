// QA Loop Engineering —— 可复用闭环脚本（由 DSH workflow 工具加载执行）
//
// 用法：把本文件内容作为 workflow 工具的 `script` 传入（本文件本身不被 node 执行）。
// 可选入参：args = { maxRounds?: number, target?: number, focus?: string }
//
// 设计要点：
// - 审计（产品/客户端/服务端/测试四组并行）→ 评测组打分 → 未达阈值则客户端+服务端并行修复 → 测试组复测；
// - 角色写权限硬隔离：客户端只改 frontend/**，服务端只改 backend/**；
// - 评测组必须先自证（真跑一条验证命令）再打分；存在未修 P0 上限 70，≥3 条 P1 上限 85；
// - 收敛即停，跑满上限也交卷，并把"未闭合项"写进返回值。

const MAX_ROUNDS = Number(args.maxRounds || 5);
const TARGET = Number(args.target || 95);
const FOCUS = String(args.focus || "");

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
          area: { type: "string" }, // 功能 | 数据一致性 | 错误处理 | 前端交互 | 架构 | 测试 | 文档
          severity: { type: "string", enum: ["P0", "P1", "P2"] },
          title: { type: "string" },
          evidence: { type: "string" }, // 必须含 文件:行号
          impact: { type: "string" },
          fix: { type: "string" },
          verify: { type: "string" },
        },
      },
    },
  },
};

const QA = {
  type: "object",
  additionalProperties: false,
  required: ["backend", "frontend", "failures"],
  properties: {
    backend: { type: "string" }, // 例：Ran 510 tests / OK
    frontend: { type: "string" }, // 例：tsc 0 错；npm test 123 pass；build 7 路由
    failures: { type: "array", items: { type: "string" } },
    acGaps: { type: "array", items: { type: "string" } },
    suggestedTests: { type: "array", items: { type: "string" } },
  },
};

const SCORE = {
  type: "object",
  additionalProperties: false,
  required: ["total", "verdict", "dimensions", "fixes"],
  properties: {
    total: { type: "number" },
    verdict: { type: "string" },
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
  },
};

const FIX = {
  type: "object",
  additionalProperties: false,
  required: ["scope", "filesChanged", "changes", "commands", "result"],
  properties: {
    scope: { type: "string", enum: ["frontend", "backend"] },
    filesChanged: { type: "array", items: { type: "string" } },
    changes: { type: "array", items: { type: "string" } },
    commands: { type: "array", items: { type: "string" } },
    result: { type: "string" },
    skipped: { type: "array", items: { type: "string" } },
    risk: { type: "string" },
  },
};

const REPO = "C:\\Users\\13067\\Desktop\\pm-brainstorming-workbench";
const PY = ".venv/Scripts/python.exe";

const COMMON = `
仓库：${REPO}（Windows，bash 可用；PY=${PY}）
必读：docs/qa-loop-engineering.md、docs/v4-acceptance-report.md、docs/v4-release-checklist.md、docs/v4-m1-interfaces.md
环境红线：
- 端口 8000/3000 被其他程序占用，本项目栈在 8001/3001（start.bat 拉起），禁止杀这些进程；
- npm registry 不可达：禁止 npm install / pip install；前端测试用 node --test；
- 禁止 git 写操作（commit/push/checkout）；
- 你的写权限严格受限：审计角色只读，修复角色只改自己那一侧（frontend/** 或 backend/**）。
${FOCUS ? "- 本轮额外关注：" + FOCUS : ""}
`;

function auditPrompt(title, extra) {
  return `${COMMON}
你是【${title}】。只读审计本轮代码，输出结构化发现。
要求：
- 最多 10 条发现，按严重度排序；每条必须有 文件:行号 证据、用户可见影响、最小修法、可执行的验证方式；
- 不要重复上一轮已修项，除非你能证明它仍是缺陷；
- 结论要能被独立复现（给出命令）。
${extra}`;
}

function scorePrompt(round, auditJson, retestJson) {
  return `${COMMON}
你是【评测组】。第 ${round} 轮评分。
先自证：至少真跑一条验证命令（后端全量 或 前端 tsc+npm test），并在 verdict 里写出真实数字。
评分卡（满分 100）：功能正确性 25 / 数据一致性与状态机 20 / 错误处理与边界 15 / 前端逻辑与交互 15 / 架构与代码质量 10 / 自动化测试 10 / 文档与可运维 5。
硬规则：存在未修 P0 → total ≤ 70；存在 ≥3 条 P1 → total ≤ 85；不得靠改期望值/删用例变绿。
上一轮复测：${retestJson ? JSON.stringify(retestJson).slice(0, 4000) : "（无）"}
本轮四组审计结果：${JSON.stringify(auditJson).slice(0, 12000)}
输出 SCORE：total、verdict、dimensions(7 项)、fixes（按优先级）、p0、p1。`;
}

function fixPrompt(scope, scoreJson, auditJson) {
  const side = scope === "frontend" ? "客户端开发组，只能改 frontend/**" : "服务端开发组，只能改 backend/**";
  return `${COMMON}
你是【${side}】修复角色。
只按评测组清单动手，最小改动：
- 不得削弱/删除既有测试，不得改期望值让测试变绿，不得新增依赖，不得 git 写；
- 每改一处都要有对应验证（后端：unittest 子集或全量；前端：npx tsc --noEmit / npm test）；
- 修不了的项写进 skipped 并说明原因，不要假装修好。
评测组清单：${JSON.stringify(scoreJson).slice(0, 8000)}
四组审计：${JSON.stringify(auditJson).slice(0, 12000)}`;
}

function retestPrompt(round) {
  return `${COMMON}
你是【测试组】。第 ${round} 轮复测。
必须真跑并逐条引用真实输出：
1) cd backend && ../${PY} -m unittest discover -s tests
2) cd frontend && npx tsc --noEmit
3) cd frontend && npm test
（时间允许再加 npm run build 与 npm run test:e2e）
把"跑不动/受限"的情况如实写入 failures，不要用推演替代真实输出。`;
}

phase("qa-loop-start");
log(`QA loop: maxRounds=${MAX_ROUNDS} target=${TARGET}`);

const rounds = [];
let finalScore = 0;
let reached95 = false;
let stopRound = 0;

for (let round = 1; round <= MAX_ROUNDS; round += 1) {
  stopRound = round;
  phase(`round-${round}-audit`);
  const audits = await parallel([
    () => agent(auditPrompt("产品组", "关注：主流程可走通性、FR/AC 一致性、入口与状态可见性、空态/加载/失败恢复。"), { schema: FINDINGS, label: `R${round} 产品组`, phase: `round-${round}-audit` }),
    () => agent(auditPrompt("客户端开发组", "关注：前端竞态、丢更新、去重、SSE 解析、版本单调、审计页与画布状态。"), { schema: FINDINGS, label: `R${round} 客户端组`, phase: `round-${round}-audit` }),
    () => agent(auditPrompt("服务端开发组", "关注：幂等、状态机、租约、预算、审计幂等、越权与软删、事务边界、错误码语义。"), { schema: FINDINGS, label: `R${round} 服务端组`, phase: `round-${round}-audit` }),
    () => agent(auditPrompt("测试组", "关注：测试覆盖缺口、跨进程/崩溃注入真实性、flake、缺失用例（给出可直接落地的用例名）。"), { schema: FINDINGS, label: `R${round} 测试组`, phase: `round-${round}-audit` }),
  ]);
  const auditSet = audits.filter(Boolean);

  phase(`round-${round}-score`);
  const previous = rounds.length ? rounds[rounds.length - 1] : null;
  const score = await agent(scorePrompt(round, auditSet, previous ? previous.retest : null), {
    schema: SCORE,
    label: `R${round} 评测组`,
    phase: `round-${round}-score`,
  });
  if (!score) {
    log(`Round ${round}: 评测组失败，终止`);
    break;
  }

  const record = { round, total: score.total, verdict: score.verdict, dimensions: score.dimensions, fixes: score.fixes, p0: score.p0, p1: score.p1, audits: auditSet, patches: [], retest: null };
  rounds.push(record);
  finalScore = score.total;
  log(`Round ${round}: ${score.total}/100`);

  if (score.total >= TARGET) {
    reached95 = true;
    break;
  }
  if (round === MAX_ROUNDS) break;

  phase(`round-${round}-fix`);
  const patches = await parallel([
    () => agent(fixPrompt("frontend", score, auditSet), { schema: FIX, label: `R${round} 客户端修复`, phase: `round-${round}-fix` }),
    () => agent(fixPrompt("backend", score, auditSet), { schema: FIX, label: `R${round} 服务端修复`, phase: `round-${round}-fix` }),
  ]);
  record.patches = patches.filter(Boolean);

  phase(`round-${round}-retest`);
  record.retest = await agent(retestPrompt(round), {
    schema: QA,
    label: `R${round} 复测`,
    phase: `round-${round}-retest`,
  });
}

phase("qa-loop-done");
return {
  reached95,
  finalScore,
  stopRound,
  rounds,
  summary: `QA loop 结束：${stopRound} 轮，最终 ${finalScore}/100（目标 ${TARGET}）`,
};