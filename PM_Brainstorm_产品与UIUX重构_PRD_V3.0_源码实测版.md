# PM Brainstorm 产品与 UI/UX 重构 PRD V3.0
## 源码实测版｜核心状态机修复 × Multi-Agent 决策编排 × AI 审计可信化 × Design System 收敛

**文档版本：** V3.0  
**文档日期：** 2026-07-26  
**文档性质：** 产品需求文档 / UIUX 重构任务书 / 前后端联调验收基线  
**产品：** PM Brainstorm  
**适用角色：** 产品经理、UX 设计师、UI 设计师、前端工程师、后端工程师、AI 工程师、测试工程师、数据分析、运维/安全  
**源码测试对象：** `pm-brainstorming-workbench.zip`

---

# 0. 文档结论先行

本次不是基于界面截图进行审美评价，而是对用户提交的源码包进行了源码检查、可运行性测试、状态机模拟和核心交互链路验证。

结论非常明确：

> **当前源码已经具备一个“可以展示产品概念的核心 MVP”，但尚未具备稳定的“产品决策操作系统”级状态机。当前最优先的任务不是继续增加 Decision Center、PRD、Roadmap 等新页面，而是先修复“澄清 → 多 Agent 推演 → 画布 → AI 审计”这一条主链路的真实性、连续性和可追溯性。**

从源码实际行为看，当前最危险的问题集中在四处：

1. **产品教练并没有真正形成多轮澄清状态。**
2. **四 Agent 的“全部讨论”是顺序串行、相互污染上下文的，并非真正独立交叉推演。**
3. **讨论地图并不是一个真正的 Decision Graph，且存在更新丢失风险。**
4. **AI 审计的 0/6 进度并非根据六维审计真实状态计算，而是通过 assistant 消息数量推算。**

这四个问题如果不先修复，继续在其上叠加 PRD、Roadmap、Evidence Hub、团队评审，只会让产品形成“页面很多，但决策链不可信”的结构性问题。

因此 V3.0 的总策略为：

> **先修核心判断链，再扩产品管理链；先保证状态真实，再提升视觉表现；先建立可追溯的数据模型，再增加高级界面。**

---

# 1. 本次真实测试范围

## 1.1 已完成：源码结构检查

实际检查到的前端页面只有四类：

```text
/
├── 首页
├── /product
├── /session/[id]
└── /session/[id]/interview
```

对应源码：

```text
frontend/app/page.tsx
frontend/app/product/page.tsx
frontend/app/session/[id]/page.tsx
frontend/app/session/[id]/interview/page.tsx
```

这意味着此前截图中已经设计出的：

- 决策中心
- Evidence Hub
- Validation Lab
- Roadmap
- PRD Center
- Team Review
- Agent Configuration
- Retrospective

**并不包含在本次提交的源码包中。**

因此本 PRD 明确拆成两条轨道：

### Track A｜当前源码 MVP 修复

优先修复：

> 首页 → Session → 产品教练 → 多 Agent → Canvas → AI Audit

### Track B｜Decision OS 扩展

在 Track A 稳定后，再实施：

> Evidence → Option → Experiment → Decision → PRD → Roadmap → Review → Retrospective

**防御准绳：**  
不得把没有出现在本源码中的后续页面描述为“已经真实测试”。如果线上生产版本位于其他仓库，应补充对应仓库后重新做第二轮源码验收。

---

## 1.2 已完成：TypeScript 类型检查

实际执行：

```text
tsc --noEmit
```

结果：

> **通过，没有 TypeScript 编译错误。**

但同时发现：

```text
next.config.js
```

中存在：

```js
eslint: {
  ignoreDuringBuilds: true,
},
typescript: {
  ignoreBuildErrors: true,
},
```

这意味着即使未来出现类型或 ESLint 错误，生产构建仍可能被放行。

因此：

> “当前 tsc 能通过”是积极结果，但“生产构建主动忽略类型错误”仍属于工程质量 P0 风险。

---

## 1.3 已完成：Next.js 预构建产物运行

源码压缩包包含 `.next` 预构建产物。

实际启动：

```text
next start -p 3100
```

并实际访问以下路径：

```text
/
 /product
 /session/test123
 /session/test123/interview
```

均返回：

> **HTTP 200**

说明当前前端预构建产物可以启动并提供页面。

---

## 1.4 构建测试边界

重新执行源码 `next build` 时，当前执行环境中压缩包自带的是 Windows 平台 SWC：

```text
@next/swc-win32-x64-msvc
```

而测试沙箱为 Linux，且外部依赖下载服务返回 503，无法下载 Linux 对应 SWC。

因此本次：

> **无法把 next build 的失败归因于源码。**

它属于“依赖包跨平台 + 当前环境无法下载”的执行环境限制。

但这同时暴露一个工程实践问题：

> **不应把 `node_modules` 与 `.next` 作为跨环境源码交付的一部分。**

---

## 1.5 已完成：核心状态机确定性模拟

为了避开真实模型随机性，本次使用 Fake LLM 替换模型输出，但实际调用了项目中的：

- SessionStore
- `run_coach`
- `run_ask_all`
- interviewer
- canvas parser
- 状态更新逻辑

完成：

```text
创建 Session
→ 产品教练
→ 用户回答
→ 全角色推演
→ 进入 AI Audit
→ 回答审计问题
→ 检查六维覆盖与消息状态
```

该测试揭示了本文中多项 P0 状态机错误。

---

## 1.6 未完成：真实 DeepSeek 模型调用

源码 `.env` 中存在已配置的 LLM API Key。

本次没有输出、记录或展示其值。

尝试连接：

```text
api.deepseek.com
```

当前沙箱环境 DNS 无法解析该域名，因此真实模型响应内容、延迟和流式稳定性没有完成测试。

因此本文对于：

- 模型真实延迟
- DeepSeek 返回稳定性
- 真实 Token 成本
- 真实 Prompt 表现

全部标记为：

> **待生产/联网测试，不做虚假结论。**

---

## 1.7 后端自动测试边界

源码仅发现：

```text
backend/tests/test_voice.py
```

没有发现：

- coach test
- multi-agent test
- canvas test
- interview test
- session state test
- frontend test
- E2E test

本次执行 pytest 时，测试环境缺少项目依赖中的 OpenAI SDK，而当前包源无法安装，因此 pytest 停留在 collection。

这不是业务代码失败，但说明：

> 当前项目自动化测试覆盖严重不足。

---

# 2. 源码实测后的严重问题清单

| ID | 问题 | 等级 | 是否实测/代码确认 |
|---|---|---:|---|
| BUG-01 | 角色选择 UI 实际不生效 | P0 | 代码确认 |
| BUG-02 | “跳过引导”写入空消息且不退出 Coach | P0 | 实测 |
| BUG-03 | 产品教练不读取历史回答 | P0 | 实测 + 代码确认 |
| BUG-04 | Coach 无明确完成与确认状态 | P0 | 代码确认 |
| BUG-05 | Session 初始 phase 与实际首流程冲突 | P1 | 代码确认 |
| BUG-06 | 四 Agent 实际顺序串行且互相锚定 | P0 | 代码确认 |
| BUG-07 | Canvas 多次更新存在丢失风险 | P0 | 代码确认 |
| BUG-08 | Canvas 实际不是 Decision Graph | P0 | 代码确认 |
| BUG-09 | Canvas 错误被静默吞掉 | P1 | 代码确认 |
| BUG-10 | AI Audit 0/6 进度计算失真 | P0 | 实测 + 代码确认 |
| BUG-11 | 重新进入 Audit 会重置审计状态 | P0 | 代码确认 |
| BUG-12 | Audit 纪要混入 Coach/Brainstorm 消息 | P0 | 代码确认 |
| BUG-13 | Audit 维度靠关键词后验猜测 | P0 | 代码确认 |
| BUG-14 | Audit 没有真正 Completed 状态 | P0 | 代码确认 |
| BUG-15 | “24ms”“运行中”“LIVE”是硬编码 | P0 | 代码确认 |
| BUG-16 | “视频通话”与实际能力不一致 | P0 | 代码确认 |
| BUG-17 | 大量 8/9/10px 与低对比文字 | P0 | 代码扫描 |
| BUG-18 | Streaming 每 Token smooth scroll | P1 | 代码确认 |
| BUG-19 | SSE onDone 存在双触发 | P1 | 代码确认 |
| BUG-20 | 前后端 phase 类型不一致 | P1 | 代码确认 |
| BUG-21 | Session JSON 存储无事务/无并发保护 | P0 上线 | 代码确认 |
| BUG-22 | 本源码未发现 Auth / 用户归属 / ACL | P0 上线 | 代码确认 |
| BUG-23 | API 输入缺少长度/成本/频率约束 | P0 上线 | 代码确认 |
| BUG-24 | 原始后端错误可能直接暴露到前端 | P1 | 代码确认 |
| BUG-25 | Build 主动忽略 TS/ESLint 错误 | P0 工程 | 代码确认 |
| BUG-26 | 自动化测试覆盖严重不足 | P0 工程 | 代码确认 |
| BUG-27 | 源码包内包含已配置 `.env` | P0 安全 | 代码确认 |
| BUG-28 | README 描述与实际实现存在漂移 | P1 | 代码确认 |
| BUG-29 | RAG 只进入 Brainstorm，不进入 Coach/Audit | P1/P0 企业态 | 代码确认 |
| BUG-30 | 后续 Decision OS 页面不在本源码 | 范围风险 | 代码确认 |

---

# 3. BUG-01｜角色选择器“看起来可用，实际无效”

## 3.1 真实问题

源码：

```text
frontend/components/chat/RoleSelector.tsx:21
```

点击某角色后，只执行：

```ts
input.dataset.targetRole = role;
```

但真正发送消息时：

```text
frontend/components/chat/InputBox.tsx:22
```

固定执行：

```ts
sendMessage(input.trim(), "all");
```

也就是说：

> 用户点击“CTO”之后，输入框虽然会变成“向 CTO 提问……”，但真实 API 仍然请求所有角色。

这是典型的：

> **UI State 与 Business State 不一致。**

属于 P0。

---

# RQ-01｜角色定向提问状态重构

**优先级：P0**

## 需求定义

角色选择必须成为 Zustand / React State 中的真实业务状态，而不是通过 DOM dataset 暂存。

新增：

```ts
targetRole:
  | "all"
  | "cto"
  | "designer"
  | "ops"
  | "user"
```

## 前端实现

```text
RoleSelector
      ↓ setTargetRole()
Session Store
      ↓
Input Composer
      ↓ sendMessage(content, targetRole)
API
```

不得使用：

```text
document.querySelector("textarea")
dataset
直接修改 placeholder
```

作为业务状态源。

## UI

选中角色必须有：

- Active Ring
- Role Accent
- Check / Target icon
- 当前提问对象文字

例如：

```text
正在向：CTO
[取消定向]
```

## 验收标准

1. 点击 CTO；
2. 输入“这个方案最大的技术风险是什么？”；
3. Network Request `target_role` 必须等于 `cto`；
4. 后端仅调用 CTO Agent；
5. 返回消息 `agent_role=cto`；
6. 刷新前当前 target 可保留在本页面状态，但不强制跨 Session 保留。

## 预期效果

真正支持官网所承诺的：

> “你可以随时追问任一角色。”

## 防御准绳

- 禁止 UI 显示角色 A，后端请求角色 B；
- Target Role 必须来自单一状态源；
- 所有角色按钮增加自动化测试；
- 用户发送后是否重置为 All，应作为明确产品规则，而不是偶然行为。

---

# 4. BUG-02｜“跳过引导，直接脑暴”实际破坏状态机

源码：

```text
frontend/components/chat/InputBox.tsx:54
```

执行：

```ts
sendMessage("", "all")
```

真实状态机模拟发现：

1. 空字符串被写入用户消息；
2. 四 Agent 可以被调用；
3. Session phase 仍然保持 `coach`；
4. 下一次用户输入仍走 `sendToCoach()`；
5. RoleSelector 仍可能继续隐藏。

这意味着“跳过”只是：

> 临时调用四个 Agent 一次，并没有真正进入 Brainstorm。

---

# RQ-02｜Skip Coach 必须成为显式状态迁移

**优先级：P0**

## 目标状态

```text
coach
  │
  ├── complete clarification
  │        ↓
  │    brainstorm
  │
  └── skip
           ↓
       brainstorm
```

## 新 API

建议：

```http
POST /api/session/{id}/phase
{
  "phase": "brainstorm",
  "reason": "user_skipped_clarification"
}
```

或：

```http
POST /api/coach/skip
```

## 后端行为

Skip：

- 不创建空用户消息；
- `phase = brainstorm`；
- 记录事件：

```json
{
  "type": "phase_changed",
  "from": "coach",
  "to": "brainstorm",
  "reason": "user_skip"
}
```

- 可让 AI 生成一句风险提示：

> “你跳过了上下文澄清，后续部分判断的可信度可能较低。”

## UI

点击：

> 跳过引导，直接脑暴

弹出轻量 Popover：

> 跳过后可以直接进入四角色推演，但部分判断可能缺少用户、替代方案和成功指标上下文。

操作：

```text
继续澄清
跳过并进入脑暴
```

## 验收标准

- 不产生空 Message；
- Phase 真实变为 brainstorm；
- Role Selector 立即可见；
- 刷新后仍处于 brainstorm；
- 后端 Session phase 与 UI phase 一致。

## 防御准绳

不允许以“发送空消息”的方式模拟任何状态迁移。

---

# 5. BUG-03｜产品教练实际上没有多轮记忆

核心源码：

```text
backend/core/agent_loop.py:46–72
```

`run_coach()` 发送给模型的上下文主要是：

```text
system prompt + 当前 user_message
```

而没有把之前用户已经回答的：

- 目标用户
- 现有替代方案
- 产品形态
- 成功指标

作为结构化状态或历史消息重新传入。

同时：

```text
backend/core/role_prompts.py
```

当前 Prompt 还要求：

> 一次提出 3–5 个关键问题。

确定性模拟中：

- 第一次 Coach：提出 5 个问题；
- 用户回答；
- 第二次 Coach：仍再次提出同样 5 个问题。

因此当前“3–5 问澄清”不是一个真正的澄清状态机。

---

# RQ-03｜Clarification Engine 结构化重构

**优先级：P0**

## 5.1 不再把 Coach 视为普通聊天

Coach 必须拥有独立的 `clarification_state`：

```json
{
  "target_user": null,
  "core_problem": null,
  "current_alternative": null,
  "product_form": null,
  "success_metric": null,
  "business_constraint": null,
  "technical_constraint": null,
  "confidence": 0
}
```

## 5.2 默认五个核心槽位

官网已经形成产品语言，可继续使用：

1. 目标用户
2. 核心问题 / 最大痛点
3. 当前替代方案
4. 初始产品形态
5. 成功指标

不是要求每个用户机械回答五次，而是：

> AI 判断哪些槽位已经明确，缺哪个才问哪个。

---

## 5.3 每轮只问一个主问题

默认：

```text
Clarification 1 / 5

谁会最频繁使用这套企业知识库？
```

用户回答后：

```text
Clarification 2 / 5

这些用户目前找信息时，最明显的阻碍是什么？
```

而不是一次展示 5 个问题。

---

## 5.4 快速填写模式

为高级用户保留：

> 快速填写全部上下文

打开 Side Sheet：

```text
目标用户
核心问题
替代方案
产品形态
成功指标
```

从而兼顾：

- Conversational UX
- 高效率

---

## 5.5 每轮 Prompt 输入

模型输入必须包含：

```text
System Rule
+
Current Structured Clarification State
+
Recent Relevant Conversation
+
Current User Answer
```

不得只发送“当前这一句话”。

---

## 5.6 自动抽取

每次用户回答后，模型输出结构化结果：

```json
{
  "extracted": {
    "target_user": "企业产品经理"
  },
  "confidence": 0.92,
  "missing_fields": [
    "current_alternative",
    "success_metric"
  ],
  "next_question": "..."
}
```

不要输出伪精确概率给用户，内部可以保留数值。

---

## 5.7 完成态

达到以下任一条件：

### 条件 A

核心 5 槽中至少 4 个明确，且核心问题与目标用户必填。

### 条件 B

用户主动点击：

> 信息够了，开始推演

然后 AI 输出：

```text
我理解的产品问题：

目标用户：……
核心问题：……
当前替代方案：……
产品形态：……
成功标准：……

[确认并开始推演]
[修改]
```

用户确认后：

```text
coach → brainstorm
```

---

## 验收标准

- 不重复询问已明确字段；
- 3–5 个问题只是典型值，不是硬编码轮数；
- 用户修改历史答案后，Summary 同步变化；
- Refresh 后 Clarification State 恢复；
- Coach 完成后后端明确更新 Phase；
- Session History 中记录 `phase_change`。

## 预期效果

将产品教练从“包装后的 ChatGPT 问卷”升级为：

> **真正维护产品上下文的 Clarification Engine。**

## 防御准绳

- 不将自然语言历史作为唯一状态来源；
- 不依赖模型自行“记得”已经问过什么；
- 必须存在结构化 Slot State；
- 必须存在用户确认点；
- AI 不得擅自填补用户没有提供的关键事实。

---

# 6. BUG-05｜Session 初始 Phase 语义错误

当前：

```text
backend/db/session_store.py
```

创建 Session 时：

```json
"phase": "brainstorm"
```

但前端加载后立即启动 Coach，随后后端又切换：

```text
brainstorm → coach
```

这造成概念上的状态闪烁。

---

# RQ-04｜统一 Session State Machine

**优先级：P0**

建议统一状态：

```text
draft
↓
clarify
↓
brainstorm
↓
audit
↓
decision_ready
↓
completed / archived
```

其中 MVP 当前至少支持：

```text
clarify
brainstorm
audit
```

## Session 新建

从首页输入问题创建：

```json
{
  "phase": "clarify"
}
```

而不是 brainstorm。

## 前后端共享枚举

建立：

```ts
type SessionPhase =
  | "clarify"
  | "brainstorm"
  | "audit"
  | "completed"
  | "archived";
```

后端 Pydantic Enum 与前端类型必须一致。

## 防御准绳

- 不允许前端使用 `coach`，后端使用另一套名称；
- 不允许 `as "brainstorm" | "interview"` 方式强行 cast 未覆盖值；
- Phase 更改只能由显式事件触发。

---

# 7. BUG-06｜四 Agent 并非真正独立交叉推演

源码：

```text
backend/core/agent_loop.py:75+
```

`run_ask_all()` 当前按：

```text
CTO
→ Designer
→ Ops
→ User
```

顺序执行。

而每个后续 Agent 读取的 recent messages 已经包含前一个 Agent 的回答。

因此：

> 后面的 Agent 会被前面的判断锚定。

这与官网：

> “从互相冲突的立场进行交叉推演”

存在明显偏差。

---

# RQ-05｜Multi-Agent Orchestration V2

**优先级：P0**

## 7.1 Stage A：Independent First Pass

四个 Agent 使用**完全相同的冻结上下文快照**：

```text
Clarification State
+
User Current Question
+
Evidence Snapshot
```

并行生成：

- position
- rationale
- risks
- assumptions
- evidence_needed

禁止读取其他 Agent 本轮回答。

---

## 7.2 Stage B：Conflict Synthesizer

四个输出完成后，独立 Synthesizer 生成：

```json
{
  "consensus": [],
  "conflicts": [],
  "evidence_gaps": [],
  "assumptions": [],
  "recommended_debates": []
}
```

---

## 7.3 Stage C：Selective Debate

UI 默认不让四个 Agent 持续大段输出。

先显示：

### 共识

> 3/4 角色认为第一版应缩小自动执行范围。

### 核心冲突

> CTO 与商业运营对“是否第一版加入自动执行”存在分歧。

### 证据缺口

> 当前没有真实用户对“确认步骤”的接受数据。

用户选择：

```text
[展开争议]
[记录为待验证假设]
[继续下一议题]
```

---

## 7.4 Debate

只有用户展开时才进入：

```text
Round 1：立场
Round 2：反驳
Round 3：条件化结论
```

避免无休止 Agent 对话。

---

## 7.5 SSE Event

新增：

```text
round_started
agent_started
agent_delta
agent_completed
conflict_analysis_started
conflict_analysis_completed
round_completed
```

前端不再仅依赖普通 Message 解释流程状态。

---

## 预期效果

- 减少锚定偏差；
- 真实体现“互相冲突立场”；
- 降低四 Agent 长文本刷屏；
- 提高用户对产品独特性的感知。

## 防御准绳

- “多 Agent”不是四次相同 LLM 调用的视觉包装；
- 首轮判断必须独立；
- 共识必须显示“几位角色支持”，而不是系统替用户下结论；
- Agent 冲突不等于最终 Decision。

---

# 8. RQ-06｜角色输出必须结构化

**优先级：P0**

每个 Agent 首轮输出：

```json
{
  "role": "cto",
  "position": "conditional_support",
  "summary": "...",
  "rationale": [
    "..."
  ],
  "risks": [
    "..."
  ],
  "assumptions": [
    "..."
  ],
  "evidence_needed": [
    "..."
  ]
}
```

UI 可以转换成自然语言，不应完全依赖一整段 Markdown。

## 防御准绳

关键 Decision Object 必须有结构化数据，不能只存在对话文本中。

---

# 9. BUG-07｜Canvas 更新存在真实“漏更新”风险

源码：

```text
frontend/store/sessionStore.ts
```

存在全局：

```text
canvasUpdatePending
```

当一次 Canvas 更新正在进行时，后续事件直接 return。

而四 Agent 会连续产生多个 `role_done`。

结果：

> 第一个 Agent 触发 Canvas 更新后，其余 Agent 的更新可能被跳过。

同时：

```text
backend/core/canvas_parser.py
```

有已有 Tree 时仅分析最近约 4 条消息。

这两者叠加会使：

> 最终画布可能没有吸收本轮所有 Agent 输出。

---

# RQ-07｜Canvas Synchronization Queue

**优先级：P0**

## 推荐方案 A

不在每个 `role_done` 后更新。

只在：

```text
round_completed
```

后进行一次 Canvas Parse。

这是最简单、最稳定方案。

---

## 推荐方案 B

如需实时更新：

```ts
canvasStatus = "syncing"
dirty = false

新事件到达:
  if syncing:
    dirty = true
  else:
    sync()

sync完成:
  if dirty:
    dirty = false
    sync()
```

---

## 必须新增

```text
last_processed_message_id
last_processed_event_id
canvas_version
```

Canvas Parser 不再依赖“最近四条”作为数据完整性策略。

## 验收

模拟四角色快速返回：

- 所有 4 个 Agent 的关键节点最终出现在 Canvas；
- 不依赖手动“生成画布”补救；
- 刷新后 Tree 与 Server 一致。

---

# 10. BUG-08｜当前 Canvas 实际不是 Decision Graph

当前 Tree 类型仅：

```text
feature
risk
question
insight
```

视觉上主要是：

> 横向 PipelineCard + 箭头。

没有真正：

- edge semantics
- source trace
- evidence linkage
- hypothesis state
- decision relationship

此外：

```text
PipelineCard.tsx
```

只展示最多 5 条，隐藏的：

> `+N 条更多`

当前只是文本，不可展开。

---

# RQ-08｜Decision Graph V1

**优先级：P0**

## Node Type

至少：

```text
Problem
User
Need
Evidence
Assumption
Constraint
Risk
Idea
Option
Decision
Experiment
Metric
```

## Edge Type

```text
supports
contradicts
depends_on
derived_from
blocks
validates
tests
measures
```

## Node Metadata

```json
{
  "id": "...",
  "type": "risk",
  "title": "...",
  "status": "draft",
  "source_message_ids": [],
  "source_agent": "cto",
  "created_by": "ai",
  "confirmed_by_user": false,
  "created_at": "...",
  "updated_at": "..."
}
```

## UI

不要求第一版立刻上复杂物理力导向图。

第一阶段推荐：

> **可缩放有向决策图 + 自动布局**

布局策略：

```text
问题
  ↓
用户需求
  ↓
候选方案
  ↓
风险 / 假设 / 证据
```

## Detail Drawer

点击节点：

- 类型
- 内容
- 来源
- 关联节点
- AI / User 标识
- Confirm
- Edit
- Reject
- Archive

## Hidden Items

`+N 条更多` 必须可展开或打开 Drawer。

## 防御准绳

- 不能只有节点没有来源；
- AI 自动生成节点默认为 Draft；
- 用户确认后才成为 Confirmed；
- 不使用复杂 3D Graph 牺牲可读性；
- Graph 是工作界面，不是数据艺术。

---

# 11. BUG-09｜Canvas 失败会被静默吞掉

当前前端 Catch 基本没有用户反馈。

如果后端 JSON Parse 失败，也可能返回：

> 解析错误 — 请重新生成

但前端 Tree 判断逻辑无法稳定把它呈现为 Error。

用户可能一直看到：

> 正在生成画布……

---

# RQ-09｜Canvas 状态模型

**优先级：P1**

新增：

```text
idle
syncing
ready
stale
error
```

### syncing

显示：

> 正在把本轮推演同步到决策图谱……

### stale

显示：

> 有新的讨论尚未同步  
> [立即同步]

### error

显示：

> 图谱同步失败，本轮对话不会丢失。  
> [重新同步]

## 防御准绳

任何“best effort”后台任务都必须有可观测状态，不能无限静默失败。

---

# 12. BUG-10｜AI Audit 0/6 进度是“伪进度”

源码：

```text
frontend/components/interview/InterviewView.tsx:18
```

当前：

```ts
const auditorTurns =
  messages.filter((message) => message.role === "assistant").length;
```

然后根据 Assistant 消息数：

```text
ceil(auditorTurns / 2)
```

推算：

```text
1/6
2/6
...
6/6
```

实际状态机测试中：

> 在真正开始审计前，Session 中已有 Coach 和四 Agent 的 assistant 消息。

因此 Audit UI 可以在根本没有完成对应维度时就显示：

> 4/6、5/6 甚至 6/6。

这是严重的真实性 Bug。

---

# RQ-10｜Audit Progress 必须由 Backend Structured State 驱动

**优先级：P0**

新增后端结构：

```json
{
  "audit_run_id": "...",
  "status": "active",
  "current_dimension": "technical_risk",
  "dimensions": {
    "problem_validity": "completed",
    "solution_validity": "active",
    "technical_risk": "pending",
    "commercial_viability": "pending",
    "user_adoption": "pending",
    "execution_risk": "pending"
  },
  "question_count": 4
}
```

前端只按照该对象展示。

不得再根据：

- 消息数量
- 角色数量
- UI 本地计数

推算审计进度。

---

# 13. BUG-11｜重新进入 Audit 会重置六维状态

后端：

```text
backend/core/interviewer.py
```

`start_interview()` 每次执行都会：

```text
interview_dimensions_covered = []
interview_question_count = 0
```

前端：

```text
/session/[id]/interview
```

页面 Mount 时自动调用：

```text
startInterview()
```

因此：

> 离开 Audit 页面再回来，会重置覆盖维度和问题计数，但历史 Audit 消息仍然存在。

这会出现：

- 视觉历史说已经问过；
- 后端状态说从头开始。

---

# RQ-11｜Audit Run 实体化

**优先级：P0**

不要把 Audit 状态直接挂在 Session 根字段。

新增：

```json
AuditRun {
  id,
  session_id,
  status,
  started_at,
  completed_at,
  current_dimension,
  dimensions,
  question_count,
  message_ids,
  result
}
```

## 页面进入逻辑

```text
进入 /interview

若存在 active audit_run
→ Resume

若不存在
→ 显示“开始新的审计”

若存在 completed run
→ 默认查看报告
→ 用户可“开始新一轮审计”
```

## 防御准绳

Page Mount 不能自动破坏后端状态。

---

# 14. BUG-12｜“审计纪要”混入整个 Session 聊天

Audit 页面当前复用全局：

```text
<MessageList />
```

而 Session messages 包含：

- Coach
- Brainstorm
- Audit

因此审计界面实际展示的并不是真正 Audit-only Conversation。

---

# RQ-12｜Message Stage Metadata

**优先级：P0**

所有 Message 增加：

```json
{
  "stage": "clarify | brainstorm | audit",
  "round_id": "...",
  "audit_run_id": null,
  "agent_role": null
}
```

Audit 页面：

```text
filter stage === "audit"
&& audit_run_id === currentAuditRun.id
```

Brainstorm 页面：

```text
stage === "clarify" OR "brainstorm"
```

历史会话可以查看全部 Timeline。

## 预期效果

同一 Session 可保留完整决策记录，但不同工作空间不会互相污染。

---

# 15. BUG-13｜六维审计覆盖是靠关键词“猜”的

当前 `_classify_dimension()` 通过：

> 生成的问题文本中出现哪些关键词

来判定当前问题属于：

- technical_risk
- user_adoption
- commercial 等。

这种方法存在：

- 关键词重叠；
- 一个问题同时命中多个维度；
- 第一匹配优先；
- 无置信度；
- 模型换一种措辞就分类失败。

---

# RQ-13｜Audit Planner

**优先级：P0**

改为：

> 先决定要审哪个维度，再让 AI 为这个维度生成问题。

结构：

```json
{
  "dimension_id": "user_adoption",
  "question": "如果用户不愿改变现有搜索习惯，你准备如何降低迁移成本？",
  "rationale": "当前方案缺少采用阻力证据",
  "expected_evidence": "迁移行为/测试反馈"
}
```

维度是 Input，不是 Output 后再猜。

---

## 六维标准

继续采用产品当前公开语言：

1. 问题有效性
2. 方案有效性
3. 技术风险
4. 商业可行性
5. 用户采用
6. 执行风险

---

## 每个 Dimension State

```text
pending
active
sufficient
insufficient_evidence
high_risk
completed
```

---

# 16. BUG-14｜Audit 缺少终止态

当前后端约束：

```text
covered >= 6
AND
question_count >= 10
```

才尝试进入总结。

但没有明确：

```text
audit_status = completed
```

因此用户后续仍可继续提交回答，再次触发模型。

同时产品 Prompt 表述“约 12–18 问”，代码最小门槛却是 10，产品方法与实现不一致。

---

# RQ-14｜Audit Completion Contract

**优先级：P0**

## 状态

```text
not_started
active
completed
aborted
superseded
```

## 完成条件

不要用“硬凑 10 问”。

建议：

### Necessary

六维都达到：

```text
sufficient
OR
insufficient_evidence
OR
high_risk
```

也就是说每个维度都被明确处理。

### Question Count

作为 Soft Range：

> 8–16 问建议范围

不作为唯一退出条件。

---

## 完成结果

```json
{
  "readiness": "conditional",
  "validated": [],
  "risks": [],
  "assumptions": [],
  "evidence_gaps": [],
  "recommended_experiments": [],
  "next_action": "..."
}
```

## UI

完成后 Composer 进入只读：

> 本轮审计已完成

操作：

```text
[查看审计报告]
[补充证据后重新审计]
```

---

# 17. BUG-15｜硬编码“24ms”“运行中”“LIVE”

源码发现：

```text
InterviewView.tsx
连接稳定 · 24ms
```

为硬编码。

产品页：

```text
Decision Signal / Live
运行中
Risk Exposure 68/100
问题证据 82%
方案聚焦 71%
技术可行 64%
商业闭环 48%
```

均为展示数据。

这些设计本身可以作为 Demo，但不能与真实 Session 状态混为一谈。

---

# RQ-15｜Truthful Status Design

**优先级：P0**

## Marketing Page

明确加：

```text
DEMO
示例审计态势
```

将：

> Decision Signal / Live

改为：

> Decision Signal / Demo

“运行中”改：

> 示例运行态

或者干脆去掉。

---

## Real Workspace

只有真实请求产生时才显示：

```text
AI 分析中
```

真实网络状态才显示：

```text
延迟 183ms
```

如果没有实际测量：

> 不显示毫秒数字。

---

## Risk Score

如果未来使用：

```text
68 / 100
```

必须点击可解释：

```text
风险暴露 68

主要贡献：
技术依赖         +22
证据不足         +18
用户采用未知     +16
商业闭环         +12

依据：
当前会话 5 条判断
Evidence 4 条

可信度：中
最近更新：14:32
```

## 防御准绳

- Demo ≠ Live；
- Estimate ≠ Fact；
- Heuristic Score ≠ Probability；
- 不生成没有真实测量来源的延迟数字。

---

# 18. BUG-16｜“视频通话”能力承诺不准确

产品页写：

> “专业视频通话体验”

当前源码能确认的是：

- 文本对话；
- Mic；
- Speech Recognition；
- TTS；
- 电话/视频形态的 UI 图标。

未发现：

- Camera Capture
- WebRTC
- Video Stream
- 摄像头权限
- 对端视频轨

因此当前源码并不支持真正的视频通话。

---

# RQ-16｜审计模态命名真实性

**优先级：P0**

短期统一：

> **AI 专业审计**

或：

> **AI 语音审计**

页面名：

> AI 审计室

按钮：

> 进入 AI 审计

而不是：

> AI 审计通话 / 视频通话

除非真实上线：

- 摄像头；
- Video Track；
- WebRTC；
- 视频权限与 Privacy。

---

# 19. Voice / Speech Trust UX

当前语音可能涉及：

- Browser Web Speech API
- 服务端 STT
- 外部模型服务
- Edge TTS

因此进入语音前要明确：

```text
语音输入模式

浏览器识别：
语音由当前浏览器提供的识别能力处理。

AI 高精度转写：
音频会发送到已配置的语音识别服务。

[了解数据处理方式]
```

## 防御准绳

不得用“本地”“安全”“不会上传”等描述，除非真实实现能够证明。

---

# 20. BUG-17｜字体尺寸与文字对比存在可访问性风险

静态扫描发现大量：

```text
text-[10px] 约 31 处
text-[9px] 约 6 处
text-[8px] 约 1 处
```

并大量使用：

```text
text-zinc-500
text-zinc-600
```

对当前接近：

```text
#080c12
```

的深色背景，粗略对比计算：

```text
zinc-500 #71717a ≈ 4.05:1
zinc-600 #52525b ≈ 2.54:1
```

均不足以作为普通小字号正文稳定满足 WCAG AA 4.5:1。

---

# RQ-17｜Typography + Contrast V3

**优先级：P0**

## 中文最低字号

### Body

14–16px

### Metadata

12–13px

### 极少量视觉标记

允许 11px，但：

- 不能承担关键意思；
- 必须高对比；
- 不建议中文。

### 禁止

8px / 9px 中文。

---

## Color Token

```text
Text Primary
rgba(255,255,255,.94)

Text Secondary
rgba(220,232,250,.74)

Text Muted
选择通过 4.5:1 实测的色值

Text Disabled
仅用于 Disabled
```

不要把 `zinc-600` 当普通正文颜色。

---

## 验收

使用自动化 contrast audit：

- 正常文本 ≥ 4.5:1；
- 大文本 ≥ 3:1；
- Focus indicator 可见；
- Status 不只依赖颜色。

---

# 21. RQ-18｜Workspace 密度与视觉收敛

**优先级：P0**

V2.0 的视觉结论继续保留，但根据源码进一步精确化。

## 当前问题

页面大量使用：

- 深色 Card
- Border
- 小字体
- Role Tag
- 装饰线
- Glow

结果是：

> 视觉“丰富”，但有效信息层级不够突出。

---

## Workspace Background

Landing：

- 星空
- Glow
- Cinema

Workspace：

- Quiet Navy
- Very Weak Grid
- No Continuous Star Animation
- Higher Surface Contrast

---

## Border

目标：

> Border 视觉使用量减少 35%–50%。

优先使用：

- spacing
- surface
- divider
- alignment

表达分组。

---

## Radius

```text
Button      10–12
Input       10–12
Small Card  12–14
Card        14–16
Large Panel 18–22
```

---

# 22. BUG-18｜流式输出每 Token smooth scroll

当前：

```text
MessageList
```

在：

- messages 变化
- streamingContent 变化

时执行 smooth scroll。

流式 Token 会持续更新 `streamingContent`。

结果：

- 每 Token 都可能触发 Scroll；
- 长回答存在不必要布局开销；
- 用户向上阅读旧消息时会被抢回底部。

---

# RQ-19｜Streaming Scroll Policy

**优先级：P1**

## 规则

只有当用户距离底部：

```text
< 120px
```

时保持自动跟随。

用户主动向上：

> 停止自动滚动。

显示 Floating：

```text
↓ 回到最新
```

Streaming 更新：

- requestAnimationFrame throttle；
- 不使用每 Token `smooth`。

## 验收

用户向上查看旧内容 10 秒：

> 页面不得自动抢回底部。

---

# 23. BUG-19｜SSE onDone 可能双触发

源码：

```text
frontend/lib/sse.ts
```

收到：

```json
{"type":"done"}
```

时触发一次 `onDone()`。

Stream 结束后又触发一次。

当前可能只是重复 setState，但会导致未来：

- Canvas 双更新；
- Analytics 双记录；
- Toast 双发；
- Phase 双跳。

---

# RQ-20｜SSE Event Contract

**优先级：P1**

## 统一终止

只能出现：

```text
done
OR
error
OR
abort
```

之一。

客户端增加：

```ts
let finalized = false;
```

所有 Finalizer：

```text
if finalized return
finalized = true
```

---

## 错误时

必须清理：

```text
isStreaming
streamingContent
streamingRole
currentRequestId
```

并保留：

> 用户输入 + 重试按钮。

---

# 24. BUG-20｜前后端状态类型漂移

前端不同文件中 Phase 类型并不完全一致。

部分类型不包含：

```text
coach
```

部分 Store 包含。

部分 loadSession 使用 Cast 规避。

---

# RQ-21｜Shared Contract

**优先级：P1**

建议建立：

```text
backend/openapi.json
        ↓
openapi-typescript
        ↓
frontend/generated/api.ts
```

至少自动生成：

- Session
- Message
- SessionPhase
- AuditState
- CanvasNode
- Event Types

## 防御准绳

核心状态枚举不得前后端各写一遍再人工同步。

---

# 25. BUG-29｜RAG 只对 Brainstorm 生效

源码确认：

```text
/api/brainstorm/message
```

会调用 RAG。

但：

- Coach
- Audit

未使用相同 Knowledge Grounding。

这意味着：

> 企业知识或外部资料可能影响脑暴，但审计时反而不知道这些证据。

---

# RQ-22｜Knowledge Grounding Policy

**优先级：P1；企业版提升 P0**

不同 Agent 明确检索策略：

## Coach

只检索：

- 用户已有项目上下文；
- 企业规则；

避免用外部资料替用户填答案。

## Brainstorm

可检索：

- 企业知识；
- 用户研究；
- 市场资料；
- 技术约束。

## Audit

重点检索：

- Evidence；
- 企业约束；
- 既有 Decision；
- 历史实验。

---

## Source Badge

AI 使用知识时显示：

```text
引用 3 条证据
```

点击展开来源。

## 防御准绳

- RAG 内容 ≠ 事实保证；
- AI 推断必须与检索文档分开；
- 文档无来源元数据时不得显示伪出处。

---

# 26. README 与真实实现漂移

README 描述了：

- React Flow
- ChromaDB

但当前 `package.json` 没有 React Flow，当前检索实现也并非 ChromaDB 向量检索。

---

# RQ-23｜Documentation Truthfulness

**优先级：P1**

每次 Release 必须同步：

```text
README
Architecture
Environment
Dependencies
API Contract
```

PR Review 添加：

> Documentation Impact

选项。

---

# 27. BUG-21｜JSON SessionStore 不适合生产并发

当前数据存储主要是：

> JSON File Read → Modify → Write。

缺少：

- transaction
- concurrency lock
- per-user ownership
- version conflict
- atomic domain operations

在单用户 Demo 可以工作。

在多人/并发环境存在：

- 写覆盖
- session 泄露
- 数据损坏
- 无法审计

风险。

---

# RQ-24｜Production Persistence Layer

**优先级：P0 上线前**

推荐：

### PostgreSQL

核心实体：

```text
User
Organization
Project
Session
Message
ClarificationState
AgentRound
AgentOpinion
DecisionNode
DecisionEdge
AuditRun
AuditQuestion
Evidence
Experiment
Decision
PRDVersion
RoadmapItem
Review
Metric
```

### Redis 可选

用于：

- Stream state
- Job progress
- Rate limit
- Temporary cache

---

## Optimistic Concurrency

关键写入携带：

```text
version
updated_at
```

避免多人覆盖。

---

# 28. BUG-22｜当前源码未发现 Auth / ACL

本次源码中没有发现完整：

- Login
- User
- Auth Middleware
- Session Ownership
- Organization ACL

而线上网站此前存在登录页。

因此存在两种可能：

### A

本源码是旧版/核心子仓库。

### B

生产 Auth 位于外层独立服务。

无论哪一种，本次源码都无法证明用户隔离已经存在。

---

# RQ-25｜Auth / Tenancy Gate

**优先级：P0 上线前**

任何 Session API 必须验证：

```text
current_user
session.user_id / org_id
permission
```

不能只凭：

```text
session_id
```

访问。

## List Sessions

不得返回系统全部 Session。

必须：

```text
WHERE user_id = current_user
OR organization ACL
```

## Shared Link

单独 Token：

```text
share_token
scope
expires_at
revoked_at
```

---

# 29. BUG-23｜输入缺少成本和滥用约束

当前主要字符串字段缺少明确：

- max length
- rate limit
- quota
- content size
- attachment limits

AI 系统中这不仅是安全问题，也是成本问题。

---

# RQ-26｜Input & Cost Guardrail

**优先级：P0**

建议初始值：

```text
Problem Statement
20–2000 chars

Chat Message
1–8000 chars

Company Context
≤ 50,000 chars / 分块处理

Attachment
限制类型 + 大小
```

## Rate Limit

按：

```text
user
org
ip
endpoint
```

分层。

## AI Request

必须有：

- request_id
- timeout
- retry policy
- token budget
- cost log

---

# 30. BUG-24｜错误信息偏开发者视角

当前 API 客户端可能直接抛：

```text
res.text()
```

Landing 错误中出现：

> 请确认后端服务已启动

对于正式用户，这是开发调试语言。

---

# RQ-27｜Error Taxonomy

**优先级：P1**

后端统一：

```json
{
  "code": "SESSION_NOT_FOUND",
  "message": "...",
  "request_id": "..."
}
```

前端映射：

### 用户可解决

> 这次会话已经失效。  
> [返回历史会话]

### 可重试

> AI 暂时没有完成响应，你的输入已经保存。  
> [重新生成]

### 无权限

> 你没有访问这个项目的权限。

### 服务异常

> 服务暂时不可用，请稍后重试。  
> Request ID: …

不得向普通用户直接输出：

- Stack Trace
- Internal Host
- Model Key
- Dependency Name
- “后端没启动”

---

# 31. BUG-25｜Build 主动忽略质量错误

当前：

```js
ignoreDuringBuilds: true
ignoreBuildErrors: true
```

---

# RQ-28｜CI Quality Gate

**优先级：P0 工程**

CI 必须：

```text
npm ci
tsc --noEmit
eslint
unit test
build
backend unit test
integration test
```

任一失败：

> PR 不允许 Merge。

生产配置删除：

```text
ignoreBuildErrors: true
```

---

# 32. BUG-26｜测试覆盖不足

当前可见自动测试主要只有 Voice。

而产品最核心的：

- Coach
- Roles
- Canvas
- Audit

几乎没有自动化保护。

---

# RQ-29｜测试金字塔

**优先级：P0**

## Backend Unit

### Clarification

- 已回答字段不重复问；
- Skip 改 Phase；
- Summary 正确；
- Confirm 后进入 Brainstorm。

### Orchestration

- 四 Agent 首轮上下文一致；
- 后 Agent 不读取本轮前 Agent 结果；
- Synthesizer 得到 4 份 Opinion。

### Audit

- 六维状态准确；
- Resume 不重置；
- Completed 后不继续追加问题。

### Canvas

- 4 Agent 输出全部同步；
- Invalid JSON Error 可恢复。

---

## Frontend

使用：

- Vitest
- React Testing Library

覆盖：

- Role Selector
- Composer
- Audit Progress
- Canvas Error
- Streaming Scroll

---

## E2E

使用 Playwright：

### Golden Flow 01

```text
首页
→ 创建 Session
→ Coach
→ 3 个澄清问题
→ Confirm
→ Brainstorm
→ 选择 CTO
→ 四角色
→ Graph
→ Audit
→ Complete
```

### Golden Flow 02

```text
Audit 中途刷新
→ Resume
→ 进度不丢失
```

### Golden Flow 03

```text
网络失败
→ 输入不丢
→ Retry
```

---

# 33. BUG-27｜源码压缩包包含真实 `.env`

本次包内发现：

```text
backend/.env
```

其中存在已配置的：

```text
LLM_API_KEY
```

本文不会展示其值。

这属于 P0 Secret Hygiene 问题。

---

# RQ-30｜Secret Rotation & Packaging

**优先级：P0，立即执行**

## 必须动作

1. 立即轮换当前压缩包中出现过的 LLM API Key；
2. 删除所有共享 ZIP 中的 `.env`；
3. 仅保留：

```text
.env.example
```

4. Production 使用：
   - Secret Manager
   - CI Environment Secret
   - Server Environment Injection

5. 对 Git History 做 Secret Scan。

## `.gitignore`

即使当前 `.gitignore` 已包含 `.env`：

> 也不能假定已经安全，因为本次交付 ZIP 仍实际包含它。

## 验收

使用：

- gitleaks
- trufflehog 或同类

CI Secret Scan 必须通过。

---

# 34. RQ-31｜History Session UX

**优先级：P1**

由于产品是“决策系统”，历史会话不能只是普通聊天历史。

列表至少显示：

```text
Project / Problem
Stage
Last Decision
Open Risk
Last Updated
```

例如：

```text
企业知识库 AI 搜索系统
Audit · 4/6
2 个高风险
昨天 18:32
```

## 搜索

支持：

- 问题关键词
- Decision
- Agent
- Stage

## 防御准绳

不以聊天标题作为唯一历史检索单位。

---

# 35. RQ-32｜首页到 Session 的上下文连续性

**优先级：P0**

用户在首页输入问题后：

```text
Create Session
→ phase=clarify
→ problem_statement 保留
→ Coach 第一个问题基于该 Problem
```

若生产存在登录：

```text
首页输入
→ 登录
→ 恢复 Draft
→ Session
```

不得丢失。

---

# 36. RQ-33｜Agent Persona Visual System

**优先级：P1**

源码已经使用 SVG Avatar，比 Emoji 结构图标更合理。

继续统一：

```text
CTO
产品设计
商业运营
目标用户
产品教练
AI 审计
```

## Role Accent

- CTO：Blue
- Designer：Violet
- Ops：Amber
- User：Teal/Green
- Coach：Cyan
- Auditor：Deep Violet / Risk Accent

## 状态 Ring

```text
Idle
Thinking
Speaking
Challenging
Completed
```

颜色只能辅助，必须同时有状态文字或 Icon。

---

# 37. RQ-34｜Composer V3

**优先级：P0**

当前两行 Textarea 基础尚可，但需要变成统一 Composer。

## Default

1–3 行，自增长。

最大：

6–8 行。

超过内部滚动。

## Toolbar

```text
@角色
附件
语音
上下文
                     发送
```

Coach 阶段：

> Quick Reply + Free Text

Brainstorm：

> Role Target + Free Text

Audit：

> Answer + Evidence Attachment

## 状态

- Idle
- Recording
- Transcribing
- Uploading
- Sending
- AI Responding
- Error

---

# 38. RQ-35｜产品页面 Demo 与真实能力区分

**优先级：P0**

产品营销页中可以保留高级可视化，但必须明确：

```text
示例界面 / Demo
```

尤其：

- 68/100
- 82%
- 71%
- 64%
- 48%
- 17 Insight
- 09 Risk
- 06 Hypothesis

不应让访问者误认为这些数据来自当前浏览会话。

---

# 39. Decision OS Track B 实施前置条件

此前 V2.0 设计的：

- Decision Center
- Evidence Hub
- Priority Board
- Validation Lab
- PRD
- Roadmap
- Review
- Retrospective

仍然成立，但必须在以下条件满足后才进入正式开发：

### Gate 1

Clarification 状态机通过 E2E。

### Gate 2

Multi-Agent Independent Pass + Conflict Synthesizer 上线。

### Gate 3

Decision Graph 节点具备 Source IDs。

### Gate 4

Audit 六维 Progress 由真实结构状态驱动。

### Gate 5

Session 存储改为生产数据库并有用户隔离。

---

# 40. Track B 核心数据链

未来扩展必须围绕：

```text
Problem
↓
Clarification
↓
Agent Opinions
↓
Conflict
↓
Decision Nodes
↓
Evidence
↓
Options
↓
Experiments
↓
Decision
↓
PRD
↓
Roadmap
↓
Outcome
↓
Retrospective
```

绝对不能把：

> PRD、Roadmap、Review

做成与前面 AI Brainstorm 相互孤立的普通后台功能。

---

# 41. Evidence 数据模型

```json
{
  "id": "ev_...",
  "project_id": "...",
  "type": "user_research",
  "title": "...",
  "content": "...",
  "source_url": null,
  "attachment_id": null,
  "created_by": "user",
  "quality": "direct",
  "confidence": "high",
  "related_node_ids": [],
  "created_at": "..."
}
```

## 类型

- User Research
- Usage Data
- Market
- Competitor
- Technical
- Business
- Internal Knowledge

## Quality

- Direct
- Indirect
- Anecdotal
- Assumption

> Assumption 必须与 Evidence 明确分开。

---

# 42. Decision 数据模型

```json
{
  "id": "...",
  "problem_id": "...",
  "selected_option_id": "...",
  "evidence_ids": [],
  "experiment_ids": [],
  "risks": [],
  "status": "proposed",
  "decision_reason": "...",
  "owner": "...",
  "approved_by": [],
  "created_at": "..."
}
```

## 状态

```text
Draft
Proposed
Ready for Review
Approved
Rejected
Superseded
Archived
```

---

# 43. PRD 数据模型

PRD Version 必须引用：

```text
Decision IDs
Evidence IDs
Experiment Result IDs
```

首次：

> 创建首个 PRD

不得出现：

> 归档新版本

只有版本 ≥2 时才开放：

> Version Compare。

---

# 44. UX 状态机总图

```text
                 ┌─────────────┐
                 │   DRAFT     │
                 └──────┬──────┘
                        ↓
                 ┌─────────────┐
                 │   CLARIFY   │
                 └───┬─────┬───┘
                     │     │ skip
               confirm     │
                     ↓     ↓
                 ┌─────────────┐
                 │ BRAINSTORM  │
                 └──────┬──────┘
                        ↓
              Independent Opinions
                        ↓
               Conflict Synthesis
                        ↓
                 Decision Graph
                        ↓
                 ┌─────────────┐
                 │    AUDIT    │
                 └──────┬──────┘
                        ↓
                 Audit Completed
                        ↓
                 Decision Ready
                        ↓
           Evidence / Experiment / Decide
                        ↓
                 PRD / Roadmap
                        ↓
                   Outcome
                        ↓
                Retrospective
```

---

# 45. Event Model V3

SSE 不应只传文本 Delta。

建议事件：

```text
session_state
phase_changed

clarification_started
clarification_state_updated
clarification_completed

round_started
agent_started
agent_delta
agent_completed
conflict_started
conflict_completed
round_completed

graph_sync_started
graph_sync_completed
graph_sync_failed

audit_started
audit_question
audit_answer_recorded
audit_dimension_updated
audit_completed

error
done
```

每个事件：

```json
{
  "event_id": "...",
  "request_id": "...",
  "session_id": "...",
  "timestamp": "...",
  "type": "...",
  "payload": {}
}
```

---

# 46. Design System V3

## Typography

```text
Marketing H1        64–76 / 700
Workspace Title     30–36 / 650–700
Section             20–24 / 600
Card                15–17 / 600
Body                14–16 / 400
Metadata            12–13 / 400–500
Button              14–15 / 550–600
```

---

## Spacing

8pt 基线：

```text
4
8
12
16
24
32
40
48
64
80
```

---

## Surface

```text
Canvas
Surface 01
Surface 02
Elevated
Overlay
```

不要每层都画边框。

---

## Semantic Color

Brand：

- Cyan
- Blue
- Violet

System：

- Success Green
- Warning Amber
- Risk Red
- Information Cyan
- AI Processing Violet
- Neutral Slate

---

## Button

### Primary

仅用于推进工作流：

- 开始推演
- 确认上下文
- 开始审计
- 保存决策

### Secondary

Dark Surface + Border

### Tertiary

Ghost / Text

### Danger

Red subtle

---

# 47. Accessibility Gate

所有页面强制：

- 正文 contrast ≥ 4.5:1；
- Large Text ≥ 3:1；
- Keyboard Tab 顺序正确；
- Focus Ring 明确；
- Icon-only 有 aria-label；
- Form 有 visible label；
- Error 与输入字段关联；
- 颜色不是唯一状态；
- Interactive target ≥44×44；
- 相邻目标建议 ≥8px；
- 支持 `prefers-reduced-motion`。

---

# 48. Performance Gate

## 前端

- Route Code Splitting；
- 图片 WebP/AVIF；
- 非首屏 Lazy Load；
- Font `swap/optional`；
- Streaming UI 节流；
- Canvas 节点 Virtualization；
- CLS < 0.1。

## AI

必须记录：

```text
TTFT
Full Response Time
Model
Token Input
Token Output
Retry Count
Error Code
```

---

# 49. Product Analytics

## Core Funnel

```text
Landing Viewed
→ Problem Entered
→ Session Created
→ Clarification Completed
→ First Multi-Agent Round Completed
→ Conflict Viewed
→ Graph Node Confirmed
→ Audit Started
→ Audit Completed
→ Decision Saved
```

---

## 关键事件

### Clarification

```text
clarification_question_seen
clarification_answered
clarification_skipped
clarification_fast_mode
clarification_confirmed
```

### Multi-Agent

```text
role_target_selected
agent_round_started
agent_round_completed
conflict_expanded
assumption_saved
```

### Graph

```text
node_opened
node_confirmed
node_edited
node_rejected
graph_sync_failed
```

### Audit

```text
audit_started
audit_resumed
audit_dimension_completed
audit_abandoned
audit_completed
```

---

# 50. 核心体验指标

上线后不要只看 PV。

## Activation

> 完成第一次 Audit 的新用户比例。

## Clarification Completion

> 开始 Coach 后完成上下文确认的比例。

## Time to First Insight

从 Session 创建到首次：

> Agent Conflict / Key Insight

的时间。

## Decision Traceability

最终 Decision 中至少存在一个：

> Source / Evidence

的比例。

## Audit Completion

开始 Audit → Completed。

## Resume Reliability

中途退出再进入，状态成功恢复比例。

---

# 51. P0 开发任务顺序

## Sprint 0｜安全与工程基线

必须先做：

1. 轮换泄露风险 API Key；
2. 移除 `.env`；
3. CI Secret Scan；
4. 开启 TypeScript/ESLint Build Gate；
5. 增加核心测试基础；
6. 确认生产 Auth 所在仓库。

---

## Sprint 1｜状态机修复

1. Session Phase 统一；
2. ClarificationState；
3. Coach 单问制；
4. Coach Summary + Confirm；
5. Skip API；
6. Role Target 真状态；
7. Message Stage Metadata。

---

## Sprint 2｜Multi-Agent 修复

1. Independent First Pass；
2. Parallel / bounded concurrency；
3. AgentOpinion Object；
4. Conflict Synthesizer；
5. Conflict UI；
6. SSE Event Contract。

---

## Sprint 3｜Decision Graph

1. Node/Edge 数据模型；
2. Source Message IDs；
3. Round-completed Sync；
4. Sync Queue；
5. Error State；
6. Detail Drawer。

---

## Sprint 4｜Audit 修复

1. AuditRun；
2. Explicit Dimension Planner；
3. 真实 0/6；
4. Resume；
5. Audit-only Messages；
6. Completion State；
7. Report。

---

## Sprint 5｜视觉与可访问性

1. 字号清理；
2. Muted Contrast；
3. Workspace Density；
4. Border 减量；
5. Agent Visual；
6. Scroll Behavior；
7. Reduced Motion。

---

# 52. P1 开发任务

1. Knowledge Grounding Policy；
2. Evidence Hub；
3. Priority Board；
4. Validation Lab；
5. Analytics；
6. History Session Intelligence；
7. Responsive；
8. Error Taxonomy；
9. Shared API Types。

---

# 53. P2 开发任务

1. Decision Center；
2. PRD；
3. Roadmap；
4. Team Review；
5. Retrospective；
6. Agent Configuration；
7. Version Diff；
8. 高级 Graph 动效。

---

# 54. P0 验收用例

## E2E-01｜产品教练

输入：

> 为企业知识库设计一套 AI 搜索与决策系统。

验收：

1. Session = clarify；
2. Coach 第一次只问一个主问题；
3. 用户回答目标用户；
4. 第二问不得再次询问目标用户；
5. Clarification State 出现 target_user；
6. 完成后显示 Summary；
7. Confirm 后 Phase=brainstorm。

---

## E2E-02｜Skip

1. 新建 Session；
2. 点击“跳过引导”；
3. Message 中没有空 User Message；
4. Phase=brainstorm；
5. Role Selector 显示；
6. Refresh 后仍为 brainstorm。

---

## E2E-03｜定向 CTO

1. 点击 CTO；
2. 输入问题；
3. API target_role=cto；
4. 只出现 CTO Response；
5. 其他 Agent 不执行。

---

## E2E-04｜四 Agent 独立推演

输入：

> 第一版是否应该支持自动执行？

验证：

四个 Agent 首轮请求：

> Context Snapshot Hash 必须一致。

后 Agent 不应包含前 Agent 本轮回答。

最终：

> Synthesizer 生成 Conflict。

---

## E2E-05｜Canvas 完整性

四 Agent 完成后：

- CTO 产生一个 Risk；
- Designer 产生一个 Need；
- Ops 产生一个 Business Assumption；
- User 产生一个 Adoption Concern。

Graph 最终四类全部存在。

---

## E2E-06｜Audit 真实进度

Brainstorm 中已有 20 条 Assistant Message。

进入 Audit：

> 仍必须从真实 Dimension State `0/6` 或实际当前状态开始。

不得根据总消息数显示 6/6。

---

## E2E-07｜Audit Resume

1. 完成 3/6；
2. 离开页面；
3. Refresh；
4. 再进入；

必须：

> Resume 3/6。

不得清零。

---

## E2E-08｜Audit Complete

六维均处理完：

- status=completed；
- Composer 不能继续普通问答；
- Report 固化；
- “开始新审计”创建新 AuditRun，而不是覆盖旧 Run。

---

# 55. 防御性准绳总表

## 真值准绳

> UI 展示的状态必须来自真实业务状态，不得通过消息数量、硬编码数字或视觉动画伪造。

---

## 上下文准绳

> 用户已经提供过的信息，系统必须结构化保存；不得依赖 LLM “自行记住”。

---

## Agent 准绳

> 多 Agent 的价值来自差异、冲突和条件，不来自四倍文本量。

---

## Audit 准绳

> 审计维度必须显式规划，不允许通过关键词事后猜测。

---

## Evidence 准绳

> AI 推断永远不能自动升级为 Evidence。

---

## Graph 准绳

> 每个重要节点必须可以追溯到 Source。

---

## Error 准绳

> 失败不能静默，失败也不能让用户丢失已输入内容。

---

## Security 准绳

> 任何 Secret 不得进入 ZIP、Git、浏览器或日志。

---

## Accessibility 准绳

> “高级感”不能以 8px 字号、低对比度和不可操作状态为代价。

---

## Expansion 准绳

> 新增 PRD/Roadmap 等模块前，必须先证明核心 Decision State Machine 稳定。

---

# 56. 本轮发现与 V2.0 的关键修订关系

V2.0 中提出的多项方向，经源码测试后被证明不是单纯“审美建议”，而是实际业务逻辑需求：

### V2：建议渐进式 3–5 问

V3 实测：

> 当前 Coach 确实一次问 5 个，而且下一轮不读历史答案。

因此升级为 P0 状态机重构。

---

### V2：建议 Multi-Agent Conflict UX

V3 实测：

> 当前四 Agent 串行运行，后角色读取前角色回答。

因此升级为：

> Independent First Pass + Conflict Synthesizer。

---

### V2：建议真实六维 Audit

V3 实测：

> 当前前端 0/6 由 assistant 数量估算；后端维度靠关键词猜。

因此升级为 P0 Backend Contract。

---

### V2：建议 Decision Graph

V3 实测：

> 当前 Canvas 只是 feature/risk/question/insight 横向 Pipeline，而且存在漏更新风险。

因此升级为：

> Node/Edge/Source ID 数据模型重构。

---

### V2：建议 Truthful UI

V3 实测：

> 24ms、LIVE、Risk 68/100 等确实为硬编码展示值。

因此升级为强制真值规范。

---

# 57. 当前产品成熟度判断

以源码而非视觉稿判断：

| 层面 | 当前成熟度 | 评价 |
|---|---:|---|
| 品牌视觉 | 7.5/10 | 已形成稳定方向 |
| Landing | 7.5/10 | 有吸引力 |
| Chat 基础 | 6.5/10 | 可工作 |
| Clarification | 3.5/10 | 状态机缺失 |
| Multi-Agent | 4.0/10 | 有角色但编排弱 |
| Decision Graph | 3.5/10 | 目前仍是摘要 Pipeline |
| Audit | 3.5/10 | 概念强，真实进度逻辑弱 |
| State Consistency | 3.5/10 | 多处 UI/后端不一致 |
| Accessibility | 5.0/10 | 已有部分 aria/44px，但字号和对比有明显风险 |
| Engineering QA | 3.5/10 | 核心自动测试不足 |
| Security Hygiene | 3.0/10 | ZIP 包含已配置 Secret 是高风险 |
| Decision OS 完整性 | 3.0/10 | 后续模块不在本源码 |

综合判断：

> **当前不是“UI 再优化 20% 就完成”的产品，而是已经完成品牌层和概念层约 70%，但核心决策状态机只完成约 35%–45%。**

下一轮研发的重心应当明确从：

> “继续增加页面”

切换到：

> **“让已有四个页面真正可靠、连续、可追溯。”**

---

# 58. 最终 North Star

PM Brainstorm 最终不应该被理解为：

> 一个有四种 AI 人格、一个画布和一个审计聊天窗口的工具。

它应该成为：

> **一个把模糊产品问题逐步转化为结构化上下文，让相互独立的专家角色形成真正冲突，再把冲突沉淀为可追溯假设、证据、风险和候选决策，并通过六维审计验证后进入产品交付与复盘的 Product Decision Operating System。**

因此所有后续需求必须服从如下链路：

```text
FRAME
  ↓
CLARIFY
  ↓
SIMULATE
  ↓
CONFLICT
  ↓
STRUCTURE
  ↓
AUDIT
  ↓
EVIDENCE
  ↓
VALIDATE
  ↓
DECIDE
  ↓
DELIVER
  ↓
MEASURE
  ↓
LEARN
```

其中最重要的产品资产不是某一个页面，而是：

> **每一个 Decision 都能够回答：它从哪里来、被谁挑战过、依赖什么证据、验证过什么、为什么最终被接受。**

这应成为 PM Brainstorm 后续所有产品、UI、UX、AI Agent、数据库和工程架构设计的最高准绳。
