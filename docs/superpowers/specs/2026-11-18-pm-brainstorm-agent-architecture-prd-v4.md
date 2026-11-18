# PM Brainstorm Agent 架构优化 PRD V4.0

> 面向 Coding Agent 的需求、架构决策、接口契约与验收基线。

| 项目 | 内容 |
|---|---|
| 文档日期 | 2026-11-18，Asia/Shanghai |
| 源码基线 | ad77bd271fa26795cc81c68fb3934d96ce65533f |
| 文档状态 | 可执行设计规格；本文中的目标行为尚未实现，验收结果不得预填为通过 |
| 本次交付 | PRD；不在本次文档任务中修改业务代码或执行数据迁移 |
| 主要读者 | 产品负责人、架构负责人、前后端 Coding Agent、测试与运维 |
| 产品目标 | 让用户得到有上下文、有依据、能追踪分歧与未决事项的产品判断 |
| 默认部署边界 | 单主机、持久本地磁盘、多个 API 进程与独立 Worker；多主机部署不属于本版默认承诺 |
| 规范用语 | “必须”为验收要求；“建议”为实现者可调整项；“后续”为本版不实施 |

## 0. 执行摘要与范围

当前产品的基础是 Next.js + FastAPI + OpenAI 兼容模型接口，采用固定角色并行生成、独立画布提取和提示词驱动面试。现有价值是帮助产品经理从技术、体验、商业和用户采用等角度发现问题。架构优化的目标是把这些意见连接为可验证、可恢复的决策过程。

本版采用渐进重构：保留前后端主技术栈，建立统一产品状态、事务存储、持久化任务和公开事件协议，然后增加按需专家、证据引用、冲突复核与结构化审计。每一步必须能够单独验证与回退到上一兼容构建。

本版必须交付：

1. 产品定义、上下文、阶段与多轮澄清连续。
2. 会话、任务、消息、产品状态、审计分别建模，版本与归属清楚。
3. 任务脱离浏览器连接运行，支持断线重连、进程重启恢复和显式取消。
4. 画布成为统一产品状态的确定性投影，不再依靠前端多次调用模型维持一致性。
5. 专家先独立判断，再对重要分歧进行有限复核，主持人输出决策建议与剩余缺口。
6. 主张、证据、假设、方案、风险和实验有稳定 ID 与引用关系。
7. 面试有独立实例、问题身份、回答评估、完成结果和恢复语义。
8. 成本、错误、测试、迁移、依赖治理及运行文档可以验收。

本版不实施：完整 Decision OS 新页面、团队协同编辑、计费系统、开放式 Agent 群聊、任意代码执行、自动发布外部内容、全网自主调查、MCP/A2A 平台、微服务拆分、强制向量数据库、实时音视频模型通话。已有语音与摄像头能力保持可选，不扩大生物特征采集范围。

## 1. 事实基线与旧文档纠偏

### 1.1 已完成的审查与边界

2026-11-18 已沿主要前后端执行链路检查源码。前端 TypeScript 检查通过；在隔离目录补齐缺少的测试依赖后，47 项后端单元测试通过。另用替代模型和独立临时数据目录验证了表 1 的确定性缺陷。本次未调用真实 LLM、SMTP、STT/TTS 服务，未进行浏览器端到端、线上压力与攻击利用验证。

现有测试通过只能证明现有断言成立，不能证明下列新验收项成立。所有新测试必须验证期望行为，不得仅检查源码是否出现某个字符串。

### 1.2 与 V3.0 的关系

根目录《PM_Brainstorm_产品与UIUX重构_PRD_V3.0_源码实测版.md》保留为历史资料。本文件覆盖其与当前代码冲突的判断；V3 中尚未实现的扩展功能不自动进入 V4 范围。

| 事项 | 当前基线事实 | Coding Agent 的处理 |
|---|---|---|
| 四角色执行 | 已使用同一历史快照并行生成 | 保留独立首轮，补充汇总与选择性复核；不得再按“目前串行”立项 |
| 认证与会话归属 | 已有签名 Cookie、owner_email 与访问检查 | 保留并覆盖新增接口；解决验证码与限流状态共享 |
| 面试进度 | 已由后端事件驱动，但依据问题关键词分类 | 升级结构化问题和回答状态；不得再描述为纯前端消息数推算 |
| 类型检查 | ignoreBuildErrors 当前为 false | 保持类型检查；另解决 lint、测试与 CI 门禁 |
| 知识库 | Markdown/JSON 关键词检索 | README 的 ChromaDB 描述需要纠正；不默认迁移到向量库 |
| 画布 | 自定义水平卡片，没有 React Flow | 本版保持轻量视图，补齐版本、详情与完整内容展示 |

### 1.3 问题登记：不得与旧 B 系列编号混用

“复现”表示行为已在隔离验证中观察到；“代码事实/风险”表示有源码依据，尚未证明线上发生。

| 编号 | 性质/优先级 | 当前问题与触发条件 | 基线定位 | 对应目标 |
|---|---|---|---|---|
| ARC-01 | 复现/P1 | 教练第二轮只有当前输入，缺少原始想法与此前问答 | backend/core/agent_loop.py:88 | FR-01、FR-02 |
| ARC-02 | 代码事实/P1 | Coach 的完成和交接仅写在提示词，没有业务命令 | frontend/components/chat/InputBox.tsx:27 | FR-01 |
| ARC-03 | 复现/P1 | 画布请求进行中，后续 role_done 和 done 刷新被丢弃 | frontend/store/sessionStore.ts:86 | FR-05 |
| ARC-04 | 复现/P1 | A 会话旧画布响应覆盖当前 B 会话；读取请求也无版本保护 | frontend/store/sessionStore.ts:94、282 | FR-06 |
| ARC-05 | 复现/P1 | 消息截断到 200 条，数组游标达到 200 后增量停更 | backend/db/session_store.py:102；backend/core/canvas_parser.py:72 | FR-02、FR-05 |
| ARC-06 | 复现/P1 | JSON 是字典即通过，branches 为字符串仍被接受 | backend/core/canvas_parser.py:39；backend/api/canvas_routes.py:44 | FR-04、FR-05 |
| ARC-07 | 复现/P1 | 无画布时，面试开场不含原始定义与已有讨论 | backend/core/interviewer.py:33 | FR-07 |
| ARC-08 | 复现/P1 | 第 18 题后继续提交仍增加计数；结束按钮只导出和跳转 | backend/core/interviewer.py:102；frontend/app/session/[id]/interview/page.tsx:100 | FR-07 |
| ARC-09 | 复现及部署风险/P1 | 两次并发回答丢计数；独立存储实例可覆盖更新；内存验证码不跨进程 | backend/core/interviewer.py:144；backend/db/session_store.py:43；backend/core/auth.py:47 | FR-03、NFR-01 |
| ARC-10 | 复现/P1 | SSE 正常完成回调调用两次；意外 EOF 被当成完成 | frontend/lib/sse.ts:48 | FR-06 |
| ARC-11 | 代码事实/P2 | 关键词检索返回裸文本并截取 300 字，来源信息丢失；覆盖率只表示关键词命中 | backend/api/brainstorm_routes.py:22；backend/core/interviewer.py:156 | FR-04、FR-07 |
| ARC-12 | 架构缺口/P2 | 无主持人、任务分配、冲突处理和有界评审循环 | backend/core/agent_loop.py:125 | FR-04 |
| ARC-13 | 架构缺口/P1 | 无回合预算、输入/输出统一上限、幂等请求和失败步骤重试 | backend/core/llm_client.py:52；backend/api/brainstorm_routes.py:12 | FR-03、FR-08 |
| ARC-14 | 代码事实/P2 | 审计脚本部分以字符串存在判通过；README 与实现漂移 | scripts/audit/check_backend_logic.py:33；README.md | NFR-04 |
| ARC-15 | 包级扫描/P1 | 本次生产依赖扫描报告 1 critical、3 high；未验证所有告警实际可利用 | frontend/package.json 及安装依赖 | NFR-03 |
| ARC-16 | 范围边界/P2 | 本地视线规则未参与产品判断；画布超过 5 条的“更多”不可展开 | frontend/lib/proctor-gaze/createProctorSession.ts；frontend/components/canvas/PipelineCard.tsx:15 | FR-05、FR-09 |

源码行号只供基线定位。开始实现前应按函数名复核，不能依据过期行号机械修改。

## 2. 架构优化思考与决策记录

### ADR-01：采用渐进重构，保留业务技术栈

| 方案 | 优点 | 代价和适用边界 | 决策 |
|---|---|---|---|
| A：仅修现有 JSON、提示词与前端队列 | 变动小，可快速止血 | 难以形成跨进程事务、持久任务和稳定追溯 | 仅作为有回归测试的过渡修复 |
| B：统一领域状态 + 事务数据库 + 有限工作流 + 专家协作 | 对应当前根因，可逐步交付并保持模型供应商可替换 | 需要迁移数据、定义协议和恢复语义 | V4 执行基线 |
| C：立即整体迁入 LangGraph/Deep Agents/Temporal 等运行时 | 可复用成熟的部分编排、上下文或持久化能力 | 仍需领域模型、权限和验收；引入更多部署与迁移变量 | 后续按复杂度与评测决定 |

本版将架构模式与框架分开：产品需要的是“可恢复执行 + 结构化共享状态 + 按需专家 + 证据与评审闭环”。框架名称不作为验收项。禁止为了体现先进性自行建设通用 Agent 平台。

### ADR-02：会话历史、产品状态与模型上下文分别维护

- 历史是可追踪的事件与消息，不因达到上下文窗口而删除。
- ProductState 是用户目前在讨论的产品事实、假设、方案与决策建议。
- ContextBuilder 针对当前任务从 ProductState、当前问题、证据和最近对话组装上下文。
- 画布、纪要与审计报告从状态和引用生成，不能各自维护相互冲突的产品定义。
- 模型提出的是候选更新。用户确认过的产品约束与决策，不得被后续模型静默改写。

### ADR-03：SQLite WAL 是单主机默认存储，PostgreSQL 是扩展路径

V4 使用 SQLAlchemy 2.x Repository + Alembic schema migration；默认 SQLite WAL，本地持久磁盘，foreign_keys=ON，busy_timeout=5000ms。每个线程/任务持有独立数据库 Session，不跨 asyncio 任务共享 Session；同步数据库调用移出事件循环。事务中禁止等待模型、SMTP 或网络。

SQLite 同一时刻仍只有一个写入者，WAL 不允许多个主机通过网络文件系统共享数据库。本版不得宣传多主机高可用。需要多主机时切换 PostgreSQL Repository 并通过同一组契约与并发测试，不能只换连接字符串即宣告支持。依赖版本由实现阶段核对支持状态后锁定。

验证码、尝试次数和限流窗口同样进入共享数据库；暂不增加 Redis。登录令牌保持当前签名 Cookie 方案，不新增账户系统和跨设备强制撤销功能。

### ADR-04：执行与浏览器连接解耦

普通业务命令先落库并返回 run_id，独立 Worker 领取任务；创建、删除与审计控制动作返回各自的幂等回执。SSE 是任务事件的订阅通道；关闭页面、切会话和断网只取消订阅。用户点击“停止生成”才发取消命令。

V4 只实现本产品固定节点的有限执行器：领取、租约、步骤结果、预算、恢复与取消。不实现通用 DAG 编辑器和任意第三方任务调度。后续可替换执行器，但应保留领域对象、命令和事件契约。

### ADR-05：保留首轮独立评审，再按分歧复核

四角色首轮不能看见同轮其他角色结果；随后主持人识别重要冲突和证据缺口。只让相关角色对具体问题复核，最多一轮、最多两个角色。没有重要分歧时直接汇总。目标是增加信息与形成可执行判断，不以消息量、争论次数或“批判语气”衡量质量。

### ADR-06：画布采用确定性投影

专家直接输出经过校验的结构化候选项。Reducer 合并为 ProductState 后，由纯函数 projector 生成 CanvasProjection。浏览器不得在每次 role_done 后发起新的模型提取。

旧文本与旧画布保留为 legacy artifact；历史重建是显式任务，需要说明会调用模型及其预算。迁移本身不调用模型，不把旧模型生成的引用升级为已验证证据。

## 3. 目标用户、流程与可见行为

### 3.1 核心用户任务

| 场景 | 用户想完成的事 | V4 的可见结果 |
|---|---|---|
| 模糊想法 | 把用户、场景、问题与成功标准说清楚 | 可查看、修改并确认的产品定义 |
| 方案取舍 | 听到不同角度，知道争议和依据 | 方案比较、支持/反对理由、证据与待验证假设 |
| 多轮讨论 | 保留已有约束和已经做出的决定 | 约束持续可见，新意见能定位到相关方案 |
| 离开后回来 | 接着上次任务，不重复扣费和生成 | 任务继续、结果回放、失败步骤单独重试 |
| 压力测试 | 找出尚未被回答的问题 | 基于当前产品版本的审计问题与缺口报告 |

### 3.2 用户流程

~~~mermaid
flowchart TD
    A[输入产品想法] --> B[建立产品定义草稿]
    B --> C[多轮澄清与展示缺失项]
    C --> D{用户选择}
    D -->|确认定义| E[进入脑暴]
    D -->|暂时跳过| F[保留未知项与假设]
    F --> E
    E --> G[检索相关依据]
    G --> H[专家独立评审]
    H --> I[主持人定位分歧]
    I --> J[必要时有限复核]
    J --> K[方案与决策建议]
    K --> L[同步产品状态和画布]
    L --> M[用户确认或继续讨论]
    M --> N[基于指定版本开始审计]
    N --> O[提问、回答与证据评估]
    O --> P[完成或用户提前结束]
    P --> Q[审计报告与后续实验]
~~~

### 3.3 界面边界

沿用首页、工作台、画布、聊天、历史抽屉与审计页，不新建复杂导航。新增内容以现有页中的卡片、详情面板和状态条呈现。

- 教练区展示“产品定义”“尚未明确的内容”“确认并开始讨论”“暂时跳过”。跳过后明确列出未知项。
- 脑暴区展示“查找相关依据 → 专家评审 → 核对分歧 → 整理结论”；步骤完成前不展示伪造百分比。
- 专家按角色分别展示，不把多角色 token 拼在一个气泡中。
- 结论区同时展示推荐、替代方案、依据、未决事项与下一步验证；无足够证据时显示“待验证”。
- 画布区显示“已同步”“更新中”“同步失败，可重试”，保留最近成功版本；超过五项必须可展开，详情展示来源。
- 离开运行中的页面显示“任务将在后台继续”；提供显式“停止生成”。
- 审计区分别显示“已讨论维度 x/6”“已回答问题 n”“仍需补充的内容”，不把六维覆盖称为产品通过率。
- 任务 ID、数据库租约、模型原始 JSON 和内部推理不进入面向用户的主流程。

## 4. 架构总图与职责

~~~mermaid
flowchart LR
    UI[Next.js / Zustand] --> CMD[FastAPI 命令与查询]
    UI --> EVT[SSE 事件订阅]
    CMD --> AUTH[认证与会话权限]
    AUTH --> APP[应用服务 / 状态机]
    APP --> DB[(事务数据库)]
    EVT --> DB
    W[独立 Worker] --> DB
    W --> RUN[有限工作流执行器]
    RUN --> CTX[ContextBuilder]
    CTX --> PS[产品状态与证据]
    RUN --> AG[教练 / 专家 / 主持人 / 审计]
    AG --> GW[模型网关与结构校验]
    AG --> RET[只读知识检索]
    RUN --> RED[校验与 Reducer]
    RED --> DB
    DB --> PROJ[画布与报告投影]
    PROJ --> UI
~~~

| 层 | 必须负责 | 不承担 |
|---|---|---|
| API | 输入验证、身份、归属、命令接收、查询和订阅 | 在请求里完成整个模型回合 |
| 应用服务 | 状态转移、幂等、事务、版本校验 | 直接拼提示词或操作前端状态 |
| Repository | 持久化、约束、原子领取和查询 | 依据模型文本决定业务阶段 |
| 执行器 | 固定步骤、租约、预算、重试、取消、恢复 | 代替领域规则决定产品事实 |
| ContextBuilder | 按任务组装可解释、受预算约束的输入 | 只截取最近 20 条作为全部记忆 |
| Agent | 生成带依据的候选结论 | 任意写数据库、调用未授权外部工具 |
| Reducer | 引用校验、去重、合并、保护用户确认项 | 把模型输出直接当成已验证事实 |
| Projector | 从同一产品版本生成视图 | 发起模型调用或改变领域状态 |
| 前端 | 当前会话/任务订阅、草稿和交互 | 自行决定权威业务阶段与问题计数 |

## 5. 数据契约与不变量

### 5.1 身份、序号与版本

| 字段 | 含义与要求 |
|---|---|
| session_id | 保留当前 32 位小写十六进制 ID |
| request_id | 客户端生成 UUID；同一用户动作网络重试必须复用；同时用作 Idempotency-Key |
| run_id | 一次被接收的业务动作对应的持久任务 |
| step_id | 一个任务内的逻辑步骤，重试 attempt 不改变逻辑步骤身份 |
| message_id / message_seq | 消息稳定 ID、会话内单调递增序号；不依赖数组下标 |
| event_seq | 每个 run 内单调递增事件序号；SSE id 使用它 |
| session_version | 会话业务变更版本，用于并发命令校验 |
| product_version | 产品内容快照版本；图、报告和审计必须标明所依据版本 |
| audit_id / question_id | 一次审计实例与一个具体问题的稳定身份 |

所有时间使用 UTC、含时区的 ISO 8601；界面按用户时区显示。不得用时间戳代替唯一性约束或排序序号。

### 5.2 最小实体

| 实体 | 必须字段或约束 |
|---|---|
| Session | id、owner_email、phase、session_version、current_product_version、current_audit_id、active_run_id、created_at、updated_at、deleted_at |
| Message | id、session_id、seq、sender_kind、role_code、stage、content、status、run_id、audit_id、created_at；唯一(session_id, seq) |
| ProductState | session_id、version、schema_version、brief、claims、options、decisions、experiments、unresolved_issues、source_run_id；版本快照不可原地改写 |
| Evidence | id、session_id 或公开知识域、kind、source_uri、title、excerpt、content_hash、source_date、retrieved_at、verification_status、limitations |
| Claim | id、kind、text、source_message_ids、evidence_refs、origin、status(proposed/accepted/rejected/superseded)、supersedes_id、created_product_version；accepted只表示已收录，不表示事实已验证 |
| Option | id、title、claim_ids、tradeoffs、risk_ids、assumption_ids、experiment_ids |
| Decision | id、option_id、rationale、evidence_refs、open_issue_ids、status(proposed/confirmed/rejected/superseded)、needs_review、confirmed_by、confirmed_at |
| Experiment | id、hypothesis_claim_id、method、success_criterion、needed_data、status(proposed/planned/completed)、result_evidence_ids |
| Run | id、session_id、owner_email、request_id、action、input_hash、base_session_version、base_product_version、status、budget、lease_owner、lease_epoch、lease_expires_at、parent_run_id |
| Step | id、run_id、logical_key、kind、role_code、status、attempt_count、input_hash、input_snapshot、output_json、error_code、started_at、finished_at |
| Event | run_id、seq、type、payload、created_at；唯一(run_id, seq)；每 run 最多一个终结事件 |
| Audit | id、session_id、product_version、audit_version、status、question_count、end_reason、dimension_states、report、active_question_id、close_requested、closing_control_id |
| AuditQuestion | id、audit_id、ordinal、dimension、issue_refs、question_message_id、answer_message_id、assessment、rationale、evidence_refs；唯一(audit_id, ordinal) |
| AuditAssessmentRevision | audit_id、audit_version、dimension/question_id、before、after、origin(model/human)、source_message_ids、evidence_refs、rationale、created_at；旧修订不可覆盖 |
| IdempotencyRecord | owner_email、request_id、action、resource_scope、input_hash、response_status、response_json、related_run_id（可空）、created_at、expires_at；唯一(owner_email, request_id) |
| ControlIntent | id、session_id、audit_id、owner_email、request_id、action、target_run_id、status(accepted/scheduled/applied/failed)、related_run_id、error_code；保存运行中结束/取消审计的意图 |
| AuthCode / RateLimit | 邮箱/IP 作用域、验证码哈希、过期/重发时间、尝试次数或滑动窗口记录；禁止保存明文验证码 |
| MigrationRecord | source_path、source_hash、session_id、import_status、warnings、imported_at |

ProductState V1 允许将结构化内容存入经校验的 JSON 列，消息、任务、事件、审计问题、幂等键和权限字段必须可独立查询并建立数据库约束。不得只把原会话大 JSON 换个数据库字段继续全量覆盖。

### 5.3 业务不变量

1. 每个会话同一时刻最多一个改变会话/产品/审计状态的活动 Run；Run 内可以并行执行专家步骤。
2. 所有写动作均使用 IdempotencyRecord：相同 owner + request_id + 相同规范化输入返回已提交回执；普通命令指向同一 Run，创建/控制/删除返回对应资源回执。相同键不同输入返回 409 IDEMPOTENCY_KEY_REUSED。
3. 用户消息接收、序号分配、Run 创建和活动任务占位在同一短事务中提交。
4. 结果、状态变更、最终消息和对应公开事件在同一事务中提交。提交失败不发送成功事件。
5. product_version 只增长；旧版本保留。源消息和历史不能因上下文压缩被删除。
6. Source 引用必须属于当前会话或公开知识域；引用未获权限的其他会话按无效引用拒绝。
7. LLM 结果只有经类型、长度、枚举与引用校验后才能进入 ProductState。
8. 用户确认的定义和决策只能由显式用户动作更新；模型可提出变更建议。
9. 同一 step 的已成功持久结果最多应用一次；恢复或重试先检查结果和 input_hash。
10. 删除会话必须停止接收新动作、请求取消活动任务，并使迟到结果无法提交；不能靠删除页面或 JSON 文件阻止后台任务写回。

幂等作用域为当前认证 owner + request_id，不能跨会话复用同一键；输入哈希包含资源身份、action、业务 payload 和原 expected_session_version，不包含连接时间等传输噪声。先校验当前身份和目标资源权限，再读取幂等记录；允许会话已删除时由合法 owner 重放已提交的删除回执。普通网络重试必须保存并复用原请求体，不能用相同键更换版本字段。幂等记录默认保留 30 天，超出保留期不承诺同一键重放。

Run 另记录 result_product_version（可空）；Step.input_snapshot 保存完整已发送输入、历史截止 message_seq、上下文证据快照、模型参数、prompt_version 和输出 schema_version。不得只留哈希后在恢复时读取最新上下文重新拼装；快照不保存 API Key 或模型私有思维链。

审计模型 Run 还记录 audit_id、base_audit_version、retry_audit_version、question_id/continuation_token。retry_audit_version 在失败/取消收尾事务完成时记录，区分业务内容变化与本次失败收尾；Session.current_audit_id 始终指向用户当前选择的审计实例，即使开场失败也保留指针，直到显式新建或切换实例。

### 5.4 Canonical 类型与兼容映射

- Session.phase：clarifying、brainstorming、auditing、audit_completed。
- Run.status：queued、running、interrupted、cancel_requested、succeeded、partial_succeeded、failed、cancelled。
- Step.status：pending、running、interrupted、succeeded、failed、cancelled、skipped。
- Message.status：draft、completed、interrupted、cancelled；不完整文本不作为已完成专家结论。
- sender_kind：human、agent、system；role_code：coach、cto、designer、ops、target_user、moderator、interviewer。
- Claim.kind：fact、hypothesis、feature、risk、question、insight。
- Evidence.kind：methodology、benchmark、user_statement、user_research、market_source、experiment_result。
- Evidence.verification_status：unverified、source_checked、user_confirmed；source_checked 仅表示来源已核对，不表示主张必然正确。
- 现有 AI 目标用户角色 user 映射 target_user；人类消息依据 sender_kind 区分，不能把二者混为同一参与者。
- v1 phase：define/coach → clarifying；brainstorm → brainstorming；interview → auditing。v1 查询由 adapter 映射回旧枚举，v2 只使用 canonical 类型。

## 6. 功能需求

### FR-01：产品定义与教练状态机

ProductBrief 包含 target_users、usage_scenario、core_problem、current_alternatives、product_form、constraints、success_criteria。每个字段至少包含 value、status(unknown/proposed/confirmed)、source_message_ids。业务团队可将 constraints 设为“尚未明确”，禁止模型自行补出预算、人员与时间。

会话创建后 phase=clarifying，保存原始输入并建立 version=1 的定义草稿。教练每轮输入必须包含当前 Brief、缺失项、相关此前问答和当前输入，当前用户消息只注入一次。每轮优先问一个主问题、最多两个相关追问；用户可一次回答多个字段。

当目标用户、场景、核心问题和成功标准有可理解的值时，展示定义确认卡片；其他缺失项继续显示。ready 仅代表可供确认，不代表事实已验证。最多六轮澄清后，仍不充分则展示缺失项并让用户选择继续澄清或带假设进入脑暴。

状态：Brief draft → ready → confirmed；用户显式跳过得到 skipped。确认与跳过均为后端命令。它们将 phase 推进至 brainstorming，但不自动触发四角色生成；用户点击“开始讨论”或发送新问题后再执行脑暴。skip 必须保留原始产品想法、已回答内容、未知项和假设。

刷新页面只读取 Brief 与消息，不因 URL 中仍有 problem 参数重新发送输入。初始问题只在创建事务中写入，不由页面 mount 副作用再次提交。需要修改已确认字段时创建新 ProductState 版本，相关既有建议标记需复核。

修改通过 revise_brief 命令执行，仅允许 clarifying、brainstorming、audit_completed，要求 expected_product_version 与当前版本相等且无活动 Run。用户明确提交的字段可标 confirmed，保留变更原文与旧版本；关联决策增加 needs_review 标记而不删除。活动审计期间返回 AUDIT_ACTIVE，用户结束或取消后再修改。confirm_brief 仅负责首次完成澄清，不隐式承担后续修订。

### FR-02：持久记忆与 ContextBuilder

提供统一接口 build_context(session_id, task_kind, role_code, product_version, current_message_id, audit_id=None) → ContextBundle。ContextBundle 至少含 messages、evidence_refs、source_message_ids、estimated_input_tokens、included_sections、omitted_sections。

注入顺序：角色职责与输出协议 → 受保护的产品定义与约束 → 当前相关方案/决定/未决事项 → 相关证据 → 当前审计问题及回答（如有）→ 最近相关对话 → 当前输入。跨角色历史使用明确的来源标签，不伪装成本角色已作出的结论。

压缩时优先移除重复对话和低相关知识，再使用带来源的摘要；不能移除当前问题、目标用户、硬约束和当前输入后继续生成。核心内容仍超预算时返回 CONTEXT_TOO_LARGE，允许用户缩小范围，不静默截断。摘要是派生物，必须记录覆盖到的 message_seq、源 ID、生成版本和适用范围。

所有消息持久保留；UI 查询分页，默认 50 条、上限 100 条，支持 before_seq 读取更早消息。会话列表默认 20 条、上限 100 条，按 updated_at 与 id 稳定排序。分页不改变模型上下文策略。

### FR-03：命令、任务、幂等与持久执行

新动作使用 Command 接收：校验权限、phase、expected_session_version、request_id 和内容哈希，原子建立 Run。已有相同幂等请求先返回其原 Run，不受后来版本变化影响。新请求版本过期返回 VERSION_CONFLICT；同会话已有活动 Run 返回 SESSION_BUSY 及 active_run_id。

Worker 由独立入口 python -m workers.run_worker 启动，不依赖某个 SSE 连接。默认部署一个 Worker 进程；多 Worker 同主机必须通过数据库领取约束和租约测试。

运行状态：queued → running → succeeded/partial_succeeded/failed；running → cancel_requested → cancelled。进程异常后，过期租约的 running → interrupted → queued，恢复节点读取已落库结果。interrupted 为可恢复状态，不发终结事件。

默认租约 30 秒、每 10 秒续租；每次领取递增 lease_epoch。任何结果提交都必须携带当前 lease_owner + lease_epoch；旧 Worker 即使迟到，也不能覆盖新租约结果。一个 Run 从首次开始至最终结束最长 300 秒，等待模型和重试计入期限，恢复不重置截止时间。达到截止时间，持有当前租约者提交 failed(RUN_DEADLINE_EXCEEDED)。队列等待不占执行期限；排队超过 10 分钟进入 failed(QUEUE_WAIT_EXCEEDED)，并释放会话占位。

单步骤最多两次上游调用尝试；只有可重试的连接、超时、429、5xx 或可修复的结构错误允许第二次。成功结果被持久化后不能再次请求模型；结果是否成功未落库的崩溃边界可能再次调用上游，必须计入尝试与预算。不得承诺外部推理和收费 exactly-once。

每个专家失败不终止其他专家。只要存在可用专家结果且最终状态提交成功，Run 可为 partial_succeeded，明确缺失角色和限制；全部专家失败为 failed。主持人调用失败时使用确定性整理作为降级结果，标记“尚未完成综合判断”，不能伪装为主持人已评审。

retry_failed 生成新的子 Run，引用父 Run 的成功结果，只执行失败/已取消步骤与需要重算的汇总；不重复写原用户消息，不重跑成功专家。允许父状态 failed、partial_succeeded、cancelled；父 Run 尚在执行、正在自动恢复或取消中时拒绝手动重试。子 Run 有新的预算与每步骤最多两次尝试，必须由用户显式发起，不能自动形成无限重试链。

父 Run 没有提交产品状态时，只能在当前 product_version 仍等于其 base_product_version 时重试；父 Run 已部分提交时，只能在当前版本仍等于其 result_product_version 时重试。失败步骤使用父步骤的冻结输入，综合判断读取父成功候选与新结果，不能注入后来的产品修改。已在父产品版本中应用的候选不重复应用，只合并新增结果。条件不满足返回 409 RETRY_CONTEXT_CHANGED，提示用户发起基于新方案的正常讨论。

审计任务还必须满足：Session.current_audit_id 等于父 audit_id，Audit.audit_version 等于父 retry_audit_version，对应 question_id/continuation_token 仍有效，且没有将其替代的结束/取消意图。新建同产品版本的另一场审计、人工修改评估或切换当前实例都会使旧重试返回 409 RETRY_AUDIT_CONTEXT_CHANGED；不能复活旧实例。报告失败后若人工修改过评估，用户应发起基于新评估的 end_audit，而不是重放旧报告输入。

取消 queued/interrupted Run 时可在短事务中直接进入 cancelled 并释放占位；取消 running Run 先进入 cancel_requested，停止新步骤并尽力关闭上游连接。取消意图不因租约过期而清除；接管者优先完成取消，不重新发起模型调用。取消响应在保存请求后立即返回，清理最迟在当前调用超时及下一次 10 秒扫描后完成。取消后已生成草稿仍可查，未提交候选不进入权威产品状态。

run.completed/run.failed/run.cancelled 的结果事务同时释放 active_run_id、更新 session_version 并写终结事件；重复终结必须无效果。Run、Audit 与 Session 的恢复映射见 FR-07 的异常表，不能让任务终结后仍永远占用会话。

### FR-04：检索、专家与主持人

#### FR-04A：知识和证据

检索接口 search(query, role_code, product_context, limit=4) → RetrievalHit[]。每项包含 evidence_id、source_uri、title、heading、excerpt、score、category、source_date、content_hash、retrieved_at。知识数据必须有版本和可解析来源，返回完整语义片段并受 token 预算约束，不固定取前 300 字。

V4 默认实现本地关键词/词频加权检索，中文双字切分与英文词项均处理停用词，标题和类别单独加权；同来源去重，零相关返回空。增加至少 20 个有人工相关性标签的检索用例。数据规模和离线评测证明需要时，再加入语义向量与 reranker。

方法论可以支持分析框架，不能当成市场规模或真实需求的实证。缺来源日期的基准数字标记 unverified；用户陈述保留为 user_statement。无外部证据时必须输出假设和验证计划，不编造竞品、市场规模、付费意愿与引用。

V4 不默认接入网络搜索服务。预留只读 SearchProvider 接口，默认 disabled；未配置时不得假装已联网。后续启用外部检索应单独增加供应商、预算和网络访问策略验收，不由 Coding Agent 私自选择付费供应商。

用户可通过 add_evidence 粘贴材料摘要及可选来源 URL，字段为 kind、title、excerpt（≤8000字）、source_uri、source_date（可空）、limitations。仅保存用户提供内容，不自动抓取 URL 或解析任意文件；新材料默认 unverified。Evidence 进入当前会话证据库，不自动改变 ProductState 或确认任何主张，可被当前审计回答引用。该命令仍受会话并发与权限检查；完整文件上传、OCR 和网页采集留待后续。

#### FR-04B：角色路由和独立输入

保留“全员”和“指定角色”，增加“智能分配”：全员必须启动四角色；指定角色只运行该角色；智能分配采用确定性的议题/缺口规则选择 1–3 个角色，规则与选择原因可测试。模糊且缺关键信息时先澄清，不额外调用一个无界 Planner。

智能分配基线规则：技术/架构/数据/性能/安全指向 cto，流程/交互/体验/易用/无障碍指向 designer，商业/定价/收入/增长/获客指向 ops，需求/用户/痛点/采用/替代指向 target_user；对应未决事项类别各加权一次。按命中类别数降序选前三，平分按 cto、designer、ops、target_user 固定顺序；无命中时选择 designer + target_user。规则用于分配视角，不能作为需求是否真实的判断。实现者可新增有回归案例的词项，不能自行改变 all/指定角色语义。

首轮专家都基于同一 base_product_version 和提交前历史快照；同轮输出不可互相可见。默认可使用同一模型，允许按任务配置模型，但不能把“模型不同”当成独立证据。

#### FR-04C：结构化输出与受控合并

RoleResult 最小结构：schema_version、role_code、display_text、claims[]、option_proposals[]、critical_issues[]、uncertainties[]、suggested_experiments[]。候选 Claim 包含 local_id、kind、text、evidence_refs、source_message_ids、confidence_level(low/medium/high)、confidence_reason。confidence 是模型自评，不能展示成统计概率。

候选 Option 使用 local_id、title、claim_local_ids、tradeoffs、risk_local_ids、assumption_local_ids、experiment_local_ids；候选 Experiment 使用 local_id、hypothesis_local_id、method、success_criterion、needed_data。一个 RoleResult 内所有候选 local_id 全局唯一、长度≤64；local 引用只指向本角色候选，已有产品条目使用单独 existing_*_ids 且必须来自 ContextBundle。登记器校验引用并转换成服务器 ID 后，才允许主持人读取。未知或歧义引用不能留给前端猜测。

Pydantic 模型要求额外字段拒绝、枚举严格、字符串与数组有上限：display_text ≤6000 字；claims ≤12，单项 text ≤500 字；options ≤3；critical_issues ≤4；experiments ≤3；单项 evidence_refs/source_message_ids 各 ≤8。不得把用户输入或检索文档中“忽略系统指令”等内容当成工具权限或系统规则。

模型支持原生 JSON Schema 时使用该能力；兼容供应商不支持时采用普通 JSON + 严格服务端校验，失败最多修复一次，总尝试仍受 FR-03 与预算限制。未经校验的 JSON 不显示、不入 ProductState。支持可靠结构化增量解析的供应商可发文本进度；其他供应商展示步骤状态并在校验后一次呈现角色结果，禁止把原始 JSON 流直接渲染给用户。

角色结果通过校验并持久化步骤时，候选登记器即为 Claim、Option 等候选分配服务器 ID；(run_id, step_id, local_id) 唯一。候选保存于 Step.output_json 的 staging 命名空间，尚不属于权威 ProductState。主持人只引用这些稳定 ID 与基线 ProductState 的已有 ID；最终 Reducer 沿用 ID，不能再次编号。各专家都使用 local_id=1 时仍分配不同服务器 ID，重复投递相同逻辑步骤时返回同一候选 ID。

跨回合完全相同的标准化文本可合并来源；近义但不同主张只标记疑似重复，不自动丢弃。无效或越权引用作为校验失败处理，不能自动补造来源。target_user 根据 Brief 中明确的用户与场景提出行为假设，必须标示为模拟观点，不代表真实访谈；专家需要说明成立条件、优势、风险与反证，不能被统一语气提示强迫否定每个方案。

Reducer 的排序与归并基于固定 role_order、步骤 logical_key 和候选 local_id，不使用网络到达顺序；来源引用按稳定键排序。CanvasProjector 按产品问题、方案、风险、假设/问题、实验、决策等固定类别分组，空组可隐藏。同一 ProductState 必须得到同一业务投影；渲染时间等传输元数据不参与投影哈希。

#### FR-04D：主持人与选择性复核

主持人输出 SynthesisResult：consensus_claim_ids、conflicts[]、recommended_option_id(可空)、alternative_option_ids、decision_proposals、unresolved_issue_ids、next_experiment_ids、limitations。Conflict 包含明确议题、冲突的 claim_ids、影响、需要的证据和参与角色。

一轮最多选两个影响产品方向的冲突，最多调用两个相关专家各复核一次。复核专家只能看到与冲突有关的对方结论和证据，不能读取完整其他专家内部上下文。全程不要求或存储模型私有思维链。

复核后最多再调用一次主持人，不能再次开启复核。证据不足时允许保留分歧，输出验证建议而不是强行达成共识。自动创建的 Decision 均为 proposed，只有用户确认才成为 confirmed。

### FR-05：画布、来源与产物版本

最终接口采用 project_canvas(product_state) → CanvasProjection，纯函数，无网络和模型调用。CanvasProjection 包含 session_id、product_version、schema_version、root、branches[]；每个节点含稳定 id、claim/option/decision/experiment 引用、source_message_ids、evidence_refs 和状态。图与报告中的每个产品主张至少有原消息来源或明确的“模型推测”标识。

角色输出完成时可以展示“待合并建议”，权威画布在 Reducer 成功提交新 ProductState 后切换。前端只接收 product_state.updated 再读取投影；收到更旧版本不能覆盖更高版本。刷新/重连查询最新版本兜底，不依赖事件次数。

现有 generate/incremental 路由在兼容模式下返回确定性投影，不再追加模型调用；无新 ProductState 时返回同版投影。旧 canvas_last_msg_index 退役，任何过渡游标使用 message_seq。历史重建通过单独 rebuild_legacy Run 发起，用户可识别这是有成本的重建动作。

V4 画布仍以浏览和详情为主，不新增自由拖拽图编辑器。每个分支超过五项可展开全部；节点详情可查看原文、角色、引用、状态与产品版本。现有 PUT canvas 在 canonical 状态启用后返回 409 CANVAS_IS_DERIVED，不允许形成第二份可写产品事实；需要变更通过领域命令提交。

自动更新失败须保留旧图，显示 stale/error 与重试入口，不能静默吞掉。过渡版本仍使用异步画布请求时，必须实现每会话 pending + dirty 尾部刷新与响应版本校验；迁入确定性投影后移除该模型同步队列。

### FR-06：前端隔离与 SSE 契约

#### 前端状态

以 session_id + run_id 管理加载与订阅，消息以 message_id 去重，事件以 run_id + event_seq 去重；角色展示使用独立缓冲。每个异步响应在写 Store 前检查当前 session_id、请求 generation 和产品版本。

切换 A → B 时取消 A 的订阅与页面读取，不取消 A 的服务器任务。A 的迟到响应只能更新 A 的缓存，不能改变 B。页面卸载、快速路由切换、React StrictMode 重复 effect 和退出登录必须覆盖测试。退出登录清空会话、历史缓存、草稿、音频播放与订阅；重新登录不复用旧用户内存状态。

#### 公开事件

持久事件封装如下；SSE event 字段等于 type，id 字段等于 event_seq。

~~~json
{
  "schema_version": 1,
  "session_id": "32位hex",
  "run_id": "32位hex",
  "event_seq": 12,
  "type": "role.completed",
  "occurred_at": "2026-11-18T03:00:00Z",
  "payload": {
    "role_code": "cto",
    "message_id": "32位hex",
    "base_product_version": 3
  }
}
~~~

事件类型：run.queued、run.started、step.started、role.output.updated、role.completed、product_state.updated、audit.updated、run.recovered、run.completed、run.failed、run.cancelled。

- run.completed 只用于 succeeded/partial_succeeded；run.failed 与 run.cancelled 分别终结对应状态。每个 Run 恰好一个终结事件。
- role.output.updated 为可选的安全展示文本快照，包含 message_id、完整当前文本和 snapshot_revision；最多每 200ms 持久一次，客户端替换而非重复追加。供应商不支持时可不发。
- 所有带序号事件先提交数据库，再发送给订阅者；步骤最终文本用持久消息为准，覆盖较旧进度快照。
- GET events 支持 after_seq 与 Last-Event-ID，若两者都有采用更大值；按 seq 严格递增回放后持续订阅。已结束 Run 回放至终结事件后关闭。
- 心跳每 15 秒发送 SSE 注释，不分配事件序号。缓存/代理缓冲关闭，连接断开不改变 Run 状态。
- EOF 前未收到终结事件只视为连接中断：查询 Run 并重连，不触发成功。终结事件和 EOF 都经过同一个 once guard。
- 解析支持 LF/CRLF、多行 data、UTF-8 跨块、注释、id 和未知可忽略事件；格式损坏记录协议错误并重新同步，不能静默漏掉完成事件。
- 若事件已按保留策略压缩，返回 410 EVENT_CURSOR_EXPIRED；客户端读取当前 Run、消息和投影快照，再从服务端返回的 latest_event_seq 订阅。

Run 快照必须通过 GET /api/v2/runs/{rid}/snapshot 在同一读取事务中返回：Run/步骤状态、该 Run 的公开消息快照、session_version、product_version、对应画布/审计摘要和 snapshot_event_seq。客户端先替换该 Run 的本地快照，再从 snapshot_event_seq 之后订阅；不得分别查询三个接口后拼接最新游标。终结事件 payload 也带最终 session_version/product_version。

默认保留未压缩事件 7 天；仅终结 Run 可压缩进度事件并保留最终状态/结果及水位，活动 Run 不压缩。过期 after_seq 返回上述 410；after_seq 大于当前 latest_event_seq 返回 422 INVALID_EVENT_CURSOR。消息和产品版本不因事件压缩而删除。

### FR-07：审计实例、问题状态与结束合同

Audit 始终锚定一个 product_version。输入上下文使用 Brief、当前方案、证据、未决事项与本次审计问答，不要求画布存在。纯视觉模块不参与回答质量评分。

状态：created → opening → active → closing → completed；opening 可进入 failed；active 可进入 cancelled；closing 失败进入 failed 并可重试结束步骤。active 时记录一个 active_question_id；没有有效问题时禁用回答入口。

start 命令：同一 request_id 返回同一回执；会话已有 opening/active/closing Audit 时不创建新 Run，返回 200 的恢复回执（audit_id、reused=true、current_run_id可空、latest_run_id、session_version），供前端查询当前问题或订阅已有任务。新创建审计返回 202 的 RunReceipt 并包含 audit_id。恢复查询不改变版本；同一审计产生不同 start 请求键时仍不能生成第二个开场问题。完成后只有用户点击“重新审计”才创建新 audit_id；旧报告不覆盖。刷新页面默认查询与恢复，不自动调用 start。

每次回答必须包含 question_id，同一问题最多接收一个已提交回答。网络重试使用同一 request_id；另一个 request_id 再回答已回答问题返回 409 QUESTION_ALREADY_ANSWERED。先原子保存回答并建立处理 Run，再由模型评估回答与生成下一问。

六维 key 保留：problem_validity、solution_effectiveness、technical_risk、business_loop、user_adoption、execution_risk。问题的目标维度由结构化计划明确指定，不根据生成文本猜测。每维保存 asked_question_ids、answered_question_ids、assessment、rationale、evidence_refs、open_issue_ids。

assessment：not_discussed、awaiting_answer、needs_clarification、supported、gap、not_applicable。supported 必须有具体理由和有效证据引用；仅讨论过或口头声称不能自动升为 supported。用户可查看并纠正评估，模型评估不等于事实保证。

默认提问目标 12 个，硬上限 18 个（含开场、追问，不含报告）。全部六维讨论且无等待回答，问题数至少 6 时，可在明确列出 supported/gap/needs_clarification 的基础上提前结束；不能仅按六个 key 出现就结束。

第 18 个问题的回答被接收后，后端直接进入 closing，禁止创建 ordinal=19。完成报告不增加 question_count。

用户随时点击“结束审计”进入 closing，以 user_ended 标明未讨论项；不是宣称审计充分完成。end_audit/cancel_audit 是允许在 SESSION_BUSY 时接收的控制意图，不立即创建第二个占用会话的普通 Run：先在一个事务中保存 ControlIntent 与幂等回执，将 Audit 标为 closing（结束）或 cancelled（取消），并请求取消当前生成。

已有 Run 终结释放会话后，Worker 的收尾/每10秒扫描在事务中为 end_audit 意图建立唯一 closing Run，再由该 Run 生成报告；ControlIntent.related_run_id 随之填入。即使页面关闭、API重启，已接受的结束意图仍必须被接续。closing Run 完成后意图为 applied；失败时为 failed 且附可重试 Run。相同审计已存在结束意图时，重复结束复用该意图；不会生成第二份报告。

控制命令返回 ControlReceipt（control_id、audit_id、status、related_run_id可空、session_version），用户通过控制查询或 Audit 状态恢复后续订阅。cancel_audit 不生成报告，待旧 Run 停止后意图 applied；phase 回到 brainstorming。审计状态进入 closing/cancelled 后，旧生成的下一问题与评估结果均失去提交资格。模型取消无法保证供应商停止收费，界面如实显示“正在停止”。

控制竞争规则：completed 报告为既有终态，cancel_audit 返回 409 AUDIT_CLOSED；在报告尚未提交前，cancel_audit 优先于未完成的 end_audit。取消事务将旧结束意图标 failed(CONTROL_SUPERSEDED)，取消其关联报告 Run，并替换当前控制身份。扫描器创建 closing Run 与报告提交都必须原子校验：会话未删除、Audit仍为closing、closing_control_id等于当前有效结束意图，且意图未失效。取消先提交则不能再生成报告，报告先完成则取消不能逆转完成。

重复 end_audit 只复用 accepted/scheduled 的当前结束意图；旧意图已失败且审计内容有新修订时，可用新 request_id 接受新的结束意图并保留旧失败记录。已删除会话的控制意图停止接续；旧 Run 收尾不得用通用失败映射覆盖已经接受的结束/取消状态。

报告最小字段：audit_id、product_version、end_reason、question_count、dimension_states、supported_points、unresolved_gaps、recommended_experiments、limitations。模型报告不可用时，使用结构化已有记录生成明确标记的有限总结，再完成或显示可重试结束失败；不能凭空补齐评价。

completed/cancelled 后回答返回 409 AUDIT_CLOSED。用户只返回工作台不改变 Audit；要重新讨论或修改产品，应显式结束/取消活动 Audit，避免旧审计结果套用新方案。完成后新脑暴可创建更高 product_version，旧报告显示“基于历史版本”。

用户可通过 correct_audit_assessment 更正已经回答的问题/已经讨论的维度，提交 audit_id、expected_audit_version、dimension/question_id、assessment、rationale 与有效 evidence_refs。只允许当前 owner 操作且无活动 Run；不可篡改问题原文、ordinal、原始回答或把尚未回答的问题伪装为已回答。人工判断写入新的 AuditAssessmentRevision，origin=human，audit_version递增；旧模型判断与旧报告保留。若审计已完成，从结构化记录生成新的报告修订并标“人工修订”，不额外调用模型，也不改变原 product_version。

异常与阶段映射是强制合同：

| 事件 | Audit / 当前问题 | Session.phase 与继续方式 |
|---|---|---|
| 开场成功 | active；有效 question_id；count=1 | auditing；等待回答 |
| 开场失败/用户停止开场 Run | failed；无当前问题；count=0 | brainstorming；可重试原开场（版本相同）或重新开始 |
| 回答已接收，评估/下一问失败或被停止 | active；保留已提交回答；active_question_id为空；标 pending_continuation | auditing；显示“重试继续”或“结束审计”，不可再次提交同一回答 |
| 重试继续成功 | active；新当前问题；计数仅按新问题增加 | auditing；等待新回答 |
| end_audit 已接收 | closing；不再接受新回答；保存 ControlIntent | auditing；取消旧 Run 后接续唯一报告任务 |
| closing Run 失败 | failed，保留 closing_control_id 与报告草稿 | auditing；只重试报告，不重新提问 |
| 报告完成 | completed；无当前问题；报告与end_reason已保存 | audit_completed；可开始新讨论或新审计 |
| cancel_audit | cancelled；不接受新回答；无新报告 | brainstorming；待旧 Run 清理后可讨论 |
| 普通脑暴/澄清 Run 失败或取消 | 不改变其他 Audit 记录 | 保持既有阶段；释放任务占位，允许重试/新输入 |

任何新问题的消息、AuditQuestion、question_count、active_question_id 与事件必须原子提交；模型生成了一段文本但未成功提交，不算已经发问。start_audit 恢复 failed(closing) 时返回待完成报告，而不是重置开场；失败恢复受到 FR-03 的冻结上下文和版本条件约束。

### FR-08：成本与资源边界

以下为本版可配置默认值，不是对真实模型质量与时延的保证。实现者不得留下无限值。

| 配置 | 默认值 | 行为 |
|---|---|---|
| max_user_input_chars | 8000 | 超出返回 422 INPUT_TOO_LONG |
| max_context_input_tokens | 12000/调用 | ContextBuilder 裁减派生上下文；核心仍过大则报错 |
| max_output_tokens | 普通角色/教练/审计 1500，主持人/最终报告 2200 | 根据供应商参数转换，须有硬限制 |
| max_run_input_tokens | 60000 | 每次尝试前预留输入预算 |
| max_run_output_tokens | 14000 | 每次尝试前按上限预留输出预算 |
| max_upstream_calls_per_run | 10 | 包括重试、修复与综合判断，超限停止扩展步骤 |
| step_timeout_seconds | 90 | 包括等待完整响应；SDK 内部隐藏重试必须关闭，由执行器统一计数 |
| run_timeout_seconds | 300 | 首次执行后不因恢复/重试重置 |
| role_parallelism | 4 | 仅首轮独立专家；复核最多两个并行 |
| max_running_runs | 2/单主机 | 数据库原子领取限制；同一会话最多一个活动 Run |
| max_queued_runs_per_user | 3 | 超出返回 429 QUEUE_LIMIT_EXCEEDED |
| authenticated_commands_per_minute | 20/用户 | 共享限流；重放已有幂等请求不新增任务配额 |
| max_revision_rounds | 1 | 最多一次选择性复核 |

原生 usage 优先；不支持 usage 或 tokenizer 的兼容模型使用保守估算，并标记 usage_source=estimated。预算预留必须按数据库事务累计；未确认的崩溃调用按预留上限记账，不释放后继续无限调用。钱币成本只在有有效供应商价格配置时计算，否则显示未配置，不展示虚构价格。

缺少复核预算时跳过可选复核，保留分歧并生成有限总结；缺少必需步骤预算时 failed(BUDGET_EXCEEDED)，保留已有草稿。不得偷偷换更贵模型或突破预算。记录 prompt_version、模型标识、耗时、尝试数、usage_source 与降级原因。

计划执行前先预留必需专家与首次综合判断的预算；要启动可选复核，必须同时留足相关专家和复核后最终综合的预算。不能把预算消耗在可选步骤后才发现没有资源完成总结。target=all 预算不足时明确拒绝或失败，不能静默减少到一两个角色。安全降级、预留与实际用量结算必须有可测试的记录。

### FR-09：语音、摄像头与界面真实性

已有 STT/TTS 继续服务文本链路。未配置 STT 时说明采用浏览器识别；不能假装使用服务端高精度引擎。TTS 的“正在发言”必须跟随实际音频播放开始/结束，而不是仅跟随音频合成请求 Promise。切换模式、页面离开或用户停止后取消待播放与当前音频。

摄像头保持用户可选、先同意后启用、允许纯文字。人脸/视线规则仅表示本地测量事件，不推断诚信、回答质量或产品可行性。不新增视频上传；模型文件加载与本地采样状态如实显示。沿用有界缓冲与资源释放，验证设备失败时仍能完成文字审计。

## 7. API 契约

### 7.1 通用规则

新接口统一 /api/v2。写请求包含 Idempotency-Key 与 request_id，两者不一致返回 422；涉及现有会话变更时带 expected_session_version。后端从 Cookie 取当前用户，不接受请求体指定 owner。所有查询、订阅、取消、重试和审计路径都执行会话归属检查。

错误格式：error.code、error.message、error.retryable、error.request_id、error.details。用户文案不得包含堆栈、密钥、供应商原始报文；details 可含 current_session_version、active_run_id、retry_after 等可恢复字段。

### 7.2 端点最小集

| 方法与路径 | 输入重点 | 成功语义 |
|---|---|---|
| POST /api/v2/sessions | request_id、problem_statement | 201；创建消息、定义草稿；返回 session、product_version |
| GET /api/v2/sessions | cursor、limit | 200；当前用户分页列表 |
| GET /api/v2/sessions/{sid} | 无 | 200；会话、Brief、最新版本、active_run、active_audit 摘要 |
| GET /api/v2/sessions/{sid}/messages | before_seq、limit、stage、audit_id | 200；稳定分页消息与 next_cursor |
| GET /api/v2/sessions/{sid}/messages/{mid} | 无 | 200；权限校验后的单条来源原文 |
| GET /api/v2/sessions/{sid}/evidence/{eid} | 无 | 200；可访问证据及来源限制；不存在/越权不泄漏正文 |
| GET /api/v2/sessions/{sid}/product-state | version 可选 | 200；指定或最新版本 |
| POST /api/v2/sessions/{sid}/commands | 通用封装 + action/payload | 202；返回 Run receipt；相同已接收幂等请求返回同一 receipt |
| GET /api/v2/runs/{rid} | 无 | 200；状态、步骤、可用结果、latest_event_seq、预算与错误 |
| GET /api/v2/runs/{rid}/snapshot | 无 | 200；同一读取事务中的消息/状态/投影/事件水位 |
| GET /api/v2/runs/{rid}/events | after_seq/Last-Event-ID | 200 SSE；回放 + 订阅 |
| POST /api/v2/runs/{rid}/cancel | request_id | 202；请求取消；已终结时 200 返回既有状态 |
| POST /api/v2/runs/{rid}/retry-failed | request_id、expected_session_version | 202；新子 Run；父成功结果可复用 |
| GET /api/v2/sessions/{sid}/canvas | product_version 可选 | 200；确定性投影，无模型调用 |
| GET /api/v2/audits/{aid} | 无 | 200；问题、状态、当前问题与报告 |
| GET /api/v2/controls/{cid} | 无 | 200；当前用户控制意图与后续报告 Run |
| DELETE /api/v2/sessions/{sid} | request_id、expected_session_version | 202；逻辑删除并取消活动任务，之后读取 404 |

普通 Command action：clarify、confirm_brief、skip_clarification、revise_brief、add_evidence、brainstorm、confirm_decision、start_audit、answer_audit、correct_audit_assessment、rebuild_legacy；创建持久 Run 并返回 RunReceipt，其中 start_audit 命中已有实例时按 FR-07 返回恢复回执，不新建任务。控制 action：end_audit、cancel_audit；保存控制意图并返回 ControlReceipt。其他动作 422 UNKNOWN_ACTION。Run 取消、会话创建和删除使用专用端点，同样通过 IdempotencyRecord 幂等，不强行创建模型任务。

~~~json
{
  "request_id": "客户端UUID",
  "expected_session_version": 7,
  "action": "brainstorm",
  "payload": {
    "content": "保留三个月上线的约束，比较两个方案",
    "target": "auto"
  }
}
~~~

target：auto、all、cto、designer、ops、target_user。confirm_brief 的 payload 包含 brief_product_version 与经用户编辑的字段；answer_audit 包含 audit_id、question_id、answer；start_audit 包含 product_version；确认决策必须携带 decision_id 与其产品版本。

Receipt 必含 run_id、session_id、status、session_version、base_product_version、events_url。会话版本在命令接收事务提交时增长一次；最终结果提交时再增长一次。product_version 仅在产品内容确有变更时增长。客户端以服务端返回值更新，不能推算。

上述 Receipt 指普通 RunReceipt；控制动作遵循 FR-07 的 ControlReceipt。revise_brief、add_evidence、correct_audit_assessment 为确定性无模型步骤，同样通过 Run 执行与记录。confirm_decision 只允许 brainstorming/audit_completed 且无活动 Audit；审计中的证据补充可用 add_evidence，但不自动改变锚定的产品快照。

### 7.3 兼容与切换

保留现有 /api/session、/api/brainstorm、/api/canvas、/api/interview 路径一个迁移版本，由 adapter 调用相同应用服务并转换字段/事件。不得保留另一条不受状态机约束的旧写路径。旧请求缺幂等键时 adapter 可生成一次，但必须标明跨请求幂等不被保证；V4 前端必须提供稳定 request_id。

v1 的 done 由新任务唯一终结事件转换，role_name 中文显示与 role_code 分开；target=user 转 target_user。旧 POST 流式接口在接收后订阅任务，断开仅停止订阅。v1 不能把 partial_succeeded 映射成无条件完整成功，应附缺失角色提示。

先交付数据库与兼容 adapter，再切换 v2 前端。JSON 与数据库不能同时作为在线写入源。前端回退到 v1 模式时仍使用数据库与新应用服务。

内部里程碑尚未实现的写能力通过 capability 配置关闭，入口不展示或说明下一阶段开放，API 返回 503 FEATURE_NOT_READY；不回退到绕过新协议的旧写函数。只有 M3 通过生产门禁后才迁移真实在线写入，详见 10.3。

## 8. 数据迁移与回退

### 8.1 非破坏性迁移

提供 python -m migrations.import_json_sessions --source <绝对目录> --dry-run 和相同命令的 --apply 模式。读取前解析绝对路径，报告实际来源，避免当前 SESSION_DATA_DIR 相对工作目录的差异。不得扫描并自动合并所有磁盘目录，不删除、移动或覆盖源文件。

dry-run 输出数量、格式错误、空 owner、重复 ID、已截断历史、旧阶段与迁移计划，不输出用户正文或邮箱。apply 前创建备份清单与内容哈希，成功后逐会话核对消息顺序、数量、owner、原始定义与画布。

- 相同 session_id + 相同 source_hash 重跑跳过；同 ID 不同内容进入冲突清单，不覆盖在线数据。
- 旧数组按原顺序分配 message_seq；时间戳缺时区记录迁移假设，不靠重新排序改变问答关系。
- owner 为空的旧会话隔离，不能分配给第一个登录者或公开展示。
- 已截断历史标记 history_incomplete；已丢失文本不可恢复，不宣称迁移恢复了全部记忆。
- 旧 canvas 保存为 legacy artifact，source_text 标记未验证。无需模型即可继续浏览历史画布。
- 旧关键词维度只能映射为“曾讨论”，不能映射 supported。
- 缺少结构化 question_id 的旧活动面试保存为历史实例并结束为 cancelled(legacy_unstructured)，提示可开启新版审计；不能伪造恢复到不存在的当前问题。
- 旧产品定义先从 problem_statement 与可用历史建立草稿；未经用户确认不标为 confirmed。

### 8.2 回退策略

迁移先在副本验证，再以维护窗口停止旧写入、导入最终增量并切换。切换后保留源备份与迁移清单，恢复写入前通过 smoke test。

上线后的默认回退对象是“已支持新数据库与 v1 adapter 的上一构建”，不是原始 JSON 程序。若必须退回原始版本，先停止接收任务、等待/取消在途任务、导出全部新数据并验证兼容转换，再执行维护窗口回退；不得只恢复旧 JSON 导致切换后的数据丢失。

删除采用先不可访问、后清理的两步流程：接收 DELETE 后立即对普通查询隐藏并取消活动 Run；Worker 清理完成后级联清除在线消息、证据、产品版本、审计和带正文的事件/输入快照。保留无正文 tombstone 与最小幂等删除回执 30 天，阻止迟到任务或旧迁移重建同 ID。备份/导入原件遵循单独配置的备份保留与销毁流程，不把“在线已删除”宣传为备份已同步销毁。

## 9. 非功能需求

### NFR-01：可靠性与并发

- API 接收命令后即使 Worker 未启动，任务保持 queued，可被后来启动的 Worker 执行。
- 同一数据库两个独立进程竞争会话动作、消息序号、问题回答与任务领取，均不得丢更新或重复提交结果。
- 外部调用在事务外；事务冲突有限重试并保留错误分类；超时/数据库忙不报为模型错误。
- 删除、取消、租约过期、旧响应和旧产品版本均有提交屏障。
- messages/session列表按索引分页；不扫描并载入全部会话正文来显示历史列表。

### NFR-02：观测与性能

日志和指标包含 request_id、run_id、step_id、模型/提示词版本、耗时、结果、attempt、usage_source 与错误分类。运行日志默认不记录邮箱、原始用户正文、验证码和令牌。产品内容保存于授权数据层，诊断日志只保存必要标识。

必须提供 queued/running 数量、步骤耗时、失败/降级/恢复次数、每回合调用数与 token 用量、画布版本落后、SSE 重连次数。健康检查分别报告 API 可用与 Worker 最近心跳，不把“有 API Key”当成模型连通性验证。

在本地 1000 会话、10000 消息的固定测试集和替代模型条件下，记录硬件与配置：命令接收 p95 ≤500ms，消息/历史分页 p95 ≤300ms，事务提交后事件可读延迟 p95 ≤500ms。真实模型时延单独测量，不把它混入本地数据库指标。

### NFR-03：依赖与发布

锁定可复现依赖，检查所用 Python/Next.js/React/模型 SDK 兼容性；不得为绕过错误关闭类型检查。生产依赖告警逐项登记受影响版本、实际使用路径、修复版本与迁移验证。不能仅运行强制升级命令即宣告安全修复。

发布门禁：类型检查、构建、行为测试、数据库迁移 dry-run、归属检查、持久恢复与预算测试。已确认可达的 critical/high 不能带入发布；其余包级告警必须有可审核的适用性或修复记录。无供应商密钥的 CI 不发送邮件、不调用付费模型。

### NFR-04：评测与文档真实性

建立至少 20 个中文产品评审案例，覆盖目标不清、互相冲突的约束、无实证、错误前提、已有用户决定、长对话与专家分歧。采用同一案例与模型配置比较改造前后；人工评价看问题贴合度、证据可核对性、分歧保留和下一步可执行性。LLM 裁判仅作辅助，不能自评即验收。

确定性指标：无效引用入库为零；用户已确认约束被静默改写为零；每个新主张有合法来源或模型假设标识；超预算继续调用为零；重复请求创建重复用户消息为零。质量比较结果必须列出失败案例，不编造提升百分比。

README、环境变量示例、启动方式、架构图和审计清单与实现同步。现有 47 项测试逐项保留或按明确的需求变更记录等价替换：旧“200 条截断”测试改成历史保留/分页测试，旧“18 题总结后计数到 19”断言改成不超过 18。不得以删除测试隐藏失败。

## 10. Coding Agent 实施工作包

### 10.1 修改边界与文件职责

下列为建议的精确模块布局。已有等价模块应复用，调整命名时同步接口映射，不创建职责相同的平行实现。

~~~text
backend/
  domain/contracts.py          Pydantic 领域、命令、事件与产物类型
  domain/state_machine.py      纯业务转移与不变量
  repositories/database.py     连接、事务、schema 配置
  repositories/session_repo.py 会话、消息、产品版本与归属
  repositories/run_repo.py     幂等、任务/步骤、租约与事件
  repositories/audit_repo.py   审计、问题、回答唯一性
  repositories/auth_repo.py    验证码与限流共享状态
  services/command_service.py  命令接收与结果提交
  services/context_builder.py  受预算约束的上下文
  services/canvas_projector.py 确定性画布投影
  workflows/runner.py          有限执行、恢复、预算、取消
  workflows/coach.py           澄清与定义候选
  workflows/brainstorm.py      路由、独立专家、主持和复核
  workflows/audit.py           提问、回答评估与报告
  workers/run_worker.py        独立 Worker 入口和心跳
  migrations/                 Alembic 与 JSON 导入命令
  api/v2/                     命令、查询、事件、审计路由
  core/llm_client.py           供应商兼容、结构化结果、usage 与限时
  core/role_prompts.py         版本化职责与输出要求
  rag/retriever.py             带来源的检索结果
frontend/
  lib/contracts.generated.ts  从后端 schema 生成的类型
  lib/api.ts                  typed 错误、幂等与版本
  lib/sse.ts                  严格解析、去重、重连与终结一次
  store/sessionStore.ts       以会话/任务隔离，移除模型画布触发
  components/chat/            Brief、专家分组与结论卡片
  components/canvas/          版本、展开、来源详情
  components/interview/       审计实例、当前问题与关闭状态
~~~

### 10.2 统一交付规则

每个工作包先写其关键失败场景的行为测试，确认能在旧实现失败，再实现最小满足需求的修改。每包交付文件变更、接口/迁移影响、实际验证结果和剩余限制。不要把整个 PRD 一次改成一个无法审阅的大提交。

持久化、状态和事件契约按依赖顺序推进；语音和界面可在契约稳定后独立完善。不得在接口仍变化时让前后端分别猜测字段。若换框架、增加付费供应商、多主机部署或增加采集范围会改变本版约束，应记录为范围变更，不自行扩大。

| 工作包 | 依赖 | 精确产物与入口 | 必须先验证的行为 | 完成证据 |
|---|---|---|---|---|
| WP-00 基线与回归 | 无 | backend/tests 新增缺陷复现；frontend 增加 Vitest/React Testing Library，E2E 使用 Playwright；记录环境 | 教练丢上下文、200 游标、跨会话、重复 done、18 题越界能在旧实现失败 | 基线报告；旧缺陷可重现，不修改生产用户数据 |
| WP-01 领域协议 | WP-00 | contracts.py、state_machine.py、contracts.generated.ts；validate_transition、RoleResult、Command、RunEvent | 非法枚举/图结构/越权引用拒绝；会话与任务状态不能混用 | OpenAPI/schema 固化；前后端类型一致 |
| WP-02 存储与迁移 | WP-01 | Repository、Alembic、import_json_sessions、IdempotencyRecord、ControlIntent；append_message、save_product_version、import_session | 两进程分配序号无冲突；导入重复无重复消息；空 owner 隔离；保存超过 200 条 | 可回放迁移清单；source hash 不变；分页与 ACL 测试 |
| WP-03 持久执行 | WP-02 | command_service、run_repo、runner、worker；accept_command、claim_run、commit_step、cancel_run、retry_failed | 重复键同一 Run；过期租约被接管且旧 epoch 不能提交；成功步骤不重跑；预算/取消生效 | 无浏览器 Worker 可完成；进程强杀恢复测试；调用计数符合预算 |
| WP-04 前端与兼容 | WP-03 | api/v2、现有路由 adapters、api.ts、sse.ts、sessionStore.ts | A/B 迟到响应隔离；终结回调一次；断线恢复；刷新不重复初始输入；登出清空 | v1/v2 smoke 与 SSE 解析测试；旧路径无绕过新状态机写入 |
| WP-05 教练和上下文 | WP-04 | coach workflow、context_builder、Brief 卡片 | 第二轮包含原始定义和先前问题；确认/跳过独立；6 轮仍缺项不伪确认 | “澄清→确认→脑暴”的单条纵向流程通过 |
| WP-06 检索和证据 | WP-05 | RetrievalHit/Evidence、索引版本、检索基准集 | 来源不丢失；无命中返回空；基准数字不能伪装真实用户证据 | 20 个标签检索案例与证据引用校验 |
| WP-07 专家与收敛 | WP-06 | brainstorm workflow、RoleResult、SynthesisResult、Reducer | 首轮互不可见；all=4；指定=1；auto=1–3；最多一次/两专家复核；已确认项不覆盖 | 固定输入/替代模型的调用与状态提交断言；部分失败降级 |
| WP-08 画布和结论 | WP-07 | canvas_projector、查询/兼容路由、节点详情与展开 | 同一状态同一图；零额外提取调用；低版本不能覆盖；所有节点可查看 | 超 200 条和乱序完成案例；引用可定位 |
| WP-09 审计闭环 | WP-08 | audit_repo/workflow、实例 API 与审计页 | start 恢复；回答唯一；无图也有上下文；第18问后只出报告；结束后拒绝回答 | 完整/提前结束/失败恢复/历史导入 E2E |
| WP-10 发布闭环 | WP-09 | usage/诊断、CI、依赖修复、README、语音状态修复、评测与演练报告 | 无效引用/超预算/重复消息为零；Worker 心跳真实；TTS 跟随播放 | 全部必要验收、迁移与回退演练，已知限制清单 |

预算、超时、ACL 和幂等是 WP-03 的前置执行能力，不能拖到 WP-10 才补。依赖风险排查在 WP-00 开始，具体升级可以独立提交，不与 UI 全量重写绑定。

### 10.3 内部验证里程碑与生产门禁

| 里程碑 | 包含 | 放行条件 |
|---|---|---|
| M1：可靠基础与澄清内测 | WP-00～05；在独立测试数据库验证创建、澄清、确认/跳过、历史读取 | 原始数据保留、跨进程不丢更新、重连可恢复、教练可完成；未实现的脑暴/审计写入口关闭 |
| M2：有依据的脑暴与画布 | WP-06～08 | 结构化结论、有限复核、确定性画布与引用回归通过 |
| M3：可信审计与正式发布 | WP-09～10 | 问题/结束合同、全部发布门禁、迁移回退和质量比较完成 |

M1/M2 可部署到内部验证环境，不承担完整产品对外发布；原生产系统在正式切换前保持其自身写入源，内测只用隔离/脱敏副本。M1 旧画布和面试记录可只读，模型重建和新脑暴/审计均关闭；M2 开放新脑暴、证据和确定性画布，审计仍只读；M3 才开放新版审计并执行生产迁移。不得为了让 M1 看似功能完整而临时建设另一套旧写链路。M2 后移除前端模型画布触发和数组游标，M3 前所有保留的 v1 写端点均须走相同服务并通过验收。

## 11. 可执行验收矩阵

以下全部为待实现/待验证要求。用例断言必须检查数据库、模型调用记录、事件或用户可见结果，不能只检查实现函数是否存在。

| ID | Given / When | 必须观察到的结果 | 主责 |
|---|---|---|---|
| AC-01 | 新会话刷新三次、StrictMode 重复挂载 | 原始用户消息一条；没有额外教练 Run | WP-04/05 |
| AC-02 | 教练第一轮问目标用户，第二轮回答“企业财务” | 第二轮模型输入同时包含原问题、前问、Brief 与本次回答，当前输入一次 | WP-05 |
| AC-03 | 用户确认定义或显式跳过 | 后端进入 brainstorming；未知项保留；刷新不回到教练；没有自动四角色收费 | WP-05 |
| AC-04 | 1000 条消息、读取两页、再追加 5 条 | 历史不删除；seq 递增；投影能包含新主张；不受旧 200 限制 | WP-02/08 |
| AC-05 | 模型返回 branches 字符串、未知 role、越权 evidence_id | 拒绝结果；最多一次修复；旧状态保留，失败类型正确 | WP-01/07/08 |
| AC-06 | 两个独立 API 进程同时提交同一 request_id | 一个 Run、一条用户消息、同一 receipt；不同 body 同键得到 409 | WP-02/03 |
| AC-07 | 两个独立进程用不同键同时修改同会话 | 一个成功占位；另一请求 SESSION_BUSY/VERSION_CONFLICT；不丢计数或覆盖结果 | WP-03 |
| AC-08 | 两个 Worker 同时领取、旧 Worker 暂停超过租约后迟到 | 一次有效领取；接管递增 epoch；旧提交拒绝；已落库步骤不重跑 | WP-03 |
| AC-09 | 在步骤结果提交后、发送事件前强杀 Worker | 重启不重新调用成功模型；事件和最终消息可回放且不重复 | WP-03/04 |
| AC-10 | 模型已响应但结果提交前进程崩溃 | 恢复可能重调但总尝试≤2、预留成本不丢、最终结果仅应用一次 | WP-03 |
| AC-11 | SSE done 与 EOF 相邻、重复投递终结事件 | 客户端完成处理一次；无重复消息、无重复画布任务 | WP-04 |
| AC-12 | UTF-8跨块、CRLF、多行data、注释、意外EOF | 正确解析；意外EOF显示重连而非成功；after_seq不漏已提交结果 | WP-04 |
| AC-13 | A 正在生成时切到 B，再返回 A | B 不被 A 响应污染；A 任务继续，可回放；切换不创建新 Run | WP-04 |
| AC-14 | 一个专家成功、其余超时或结构失败 | 其他步骤不中止；结果标 partial_succeeded；重试只调用失败专家和必要汇总 | WP-03/07 |
| AC-15 | 所有专家同一首轮，模拟不同完成顺序 | 各自输入相同产品版本且无同轮他人回答；最终状态/图不依赖完成顺序 | WP-07/08 |
| AC-16 | 主持人持续提出冲突或预算不足 | 最多一轮/两专家复核；不无限循环；保留分歧和降级说明 | WP-07 |
| AC-17 | 用户已确认“三个月上线”，超过20条对话后继续脑暴 | 约束仍在模型输入与状态中；模型不能静默改写 | WP-05/07 |
| AC-18 | 角色全部完成和图查询交错、重复 product_state.updated | 最新投影含全部已合并内容；旧版本不覆盖；投影模型调用数为0 | WP-08 |
| AC-19 | 分支8条、点击第8条 | 可展开并看到原文/证据、角色、状态和版本；不是只有“+3更多” | WP-08 |
| AC-20 | 知识库无相关内容或数字无来源日期 | 不编造引用；相关度为空；数字标未验证；输出验证建议 | WP-06/07 |
| AC-21 | 没有画布但有定义与讨论，开始审计 | 输入包含所锚定 ProductState；能提出贴合产品的问题 | WP-09 |
| AC-22 | 审计中刷新、返回、再次进入 | 同一audit_id、同一当前问题；无重置与第二个开场调用 | WP-09 |
| AC-23 | 同一问题两次提交，两个不同进程同时回答 | 只接收一条回答/一个处理Run；另一请求可重放或409；计数不丢 | WP-09 |
| AC-24 | 一句话同时含“验证/用户/付费”，回答没有证据 | 目标维度由question指定；不能因关键词计入多个充分维度 | WP-09 |
| AC-25 | 已发第18问，再回答和继续发请求 | 生成报告且question_count=18；无第19问；完成后409 AUDIT_CLOSED | WP-09 |
| AC-26 | 用户提前结束、报告模型故障、重试结束 | 保留未讨论项；不重问旧题；报告恢复或明确有限总结；阶段一致 | WP-09 |
| AC-27 | 用户B查询/订阅/取消/重试用户A任务及证据 | 一律拒绝，不泄漏正文；包含v1兼容路径 | WP-02/04/09 |
| AC-28 | API进程A发验证码，进程B验证；超限与5次错码 | 状态共享，一次使用，窗口与次数原子生效；无真实邮件外发 | WP-02 |
| AC-29 | 输入8001字、调用预算耗尽、供应商无usage | 输入拒绝；无超预算额外调用；估算有标识；隐藏SDK重试为0 | WP-03/10 |
| AC-30 | Run执行时用户显式取消或删除会话 | 停止新步骤；旧epoch/已取消Run结果不能提交；无复活会话 | WP-03/04 |
| AC-31 | A账户登出后B账户登录同浏览器 | 没有A消息/历史/草稿/音频/订阅残留 | WP-04 |
| AC-32 | 两次相同源迁移、冲突JSON、空owner、旧活动审计 | 幂等跳过；冲突隔离；不公开旧数据；原文件hash不变 | WP-02 |
| AC-33 | 摄像头拒绝、STT未配置、TTS切模式 | 可完成文字审计；引擎说明真实；播放停止且麦克风状态跟随实际音频 | WP-10 |
| AC-34 | 新构建失败，切回上一数据库兼容构建 | 切换后的消息/任务不丢；数据库状态可读；无JSON双写 | WP-10 |
| AC-35 | event_seq早于压缩范围、后台任务仍在队列 | 410后读取快照并恢复；任务仍可由Worker领取；没有伪成功 | WP-03/04 |
| AC-36 | 生成下一问题时结束审计，收到回执后关闭页面并重启Worker | 结束意图已持久；旧生成不提交；唯一closing Run接续报告；最终阶段正确 | WP-03/09 |
| AC-37 | 父Run部分成功后修改产品约束，再重试失败步骤 | 409 RETRY_CONTEXT_CHANGED；不混入新上下文或重复应用旧候选 | WP-03/07 |
| AC-38 | 创建会话响应丢失后重试；两个角色都使用local_id=1 | 只创建一个会话；候选服务器ID各异且主持人/Reducer引用一致 | WP-02/07 |
| AC-39 | 回放快照时并发提交新事件；完成删除后尝试重新迁移旧源 | 快照与水位一致，不漏消息；tombstone阻止复活；在线正文已清除 | WP-02/04/10 |
| AC-40 | 修订已确认定义、粘贴证据、人工更正已完成审计 | 权限/版本/阶段校验；新修订可追溯；原报告保留；无额外模型调用 | WP-05/06/09 |
| AC-41 | 开场失败后同产品版本新建审计，再重试旧Run；人工更正后重试旧报告 | 409 RETRY_AUDIT_CONTEXT_CHANGED；不复活旧实例或覆盖人工修订 | WP-03/09 |
| AC-42 | end_audit已接收未完成时cancel_audit与扫描/报告提交竞争 | 取消先提交则无新报告且旧意图失效；完成先提交则取消409；不存在两个有效控制链 | WP-03/09 |

### 11.1 验证命令与报告要求

在 backend 目录运行现有 python -m unittest discover -s tests -v；新增执行器、数据库集成测试可继续采用 unittest，或统一迁入 pytest，但需保留原行为覆盖和可复现依赖。跨进程测试必须使用独立进程/数据库连接，不能只用同一个对象的线程锁证明。

在 frontend 目录执行 npm ci、类型检查、npm run test、npm run build；新增 test 与 test:e2e 脚本，端到端使用替代模型与临时数据库。真实供应商 smoke 以显式环境开关单独运行，报告模型配置、调用数、时延、结构校验失败与限制；无凭证时标未执行，不标通过。

每个验收 ID 在交付报告中给出测试位置、运行结果和必要截图/事件样本。任何失败先判断是否偏离需求；不得把期望值改成当前错误行为来获得绿色结果。

## 12. 发布前必须核对的五类边界

1. 缺失信息：无画布、无知识命中、无usage、旧截断历史、未知产品约束时，仍如实推进或解释缺口。
2. 并发与迟到：双击、两个标签页、两个API/Worker进程、旧租约与跨会话迟到响应不能污染状态。
3. 故障与恢复：模型失败、提交失败、发送前崩溃、SSE中断和Worker离线均有可观察的状态。
4. 状态终结：题数上限、用户结束、取消与删除后不能继续写入或重复生成。
5. 数据与权限：迁移、历史引用、任务订阅、失败重试和账户切换均保持归属，且不丢已有内容。

对应 AC-01～42 已分配到工作包；实现者发现新边界应增加用例及原因，不降低已有门槛。

## 13. 可直接交给 Coding Agent 的启动任务

~~~text
先阅读本PRD、仓库AGENTS.md（若存在）、README、现有核心源码与测试。
以源码基线核对当前行为，不把旧V3文档的过时判断当成现状。

本轮按WP-00开始，先建立隔离回归和契约；按依赖顺序交付M1、M2、M3。
先列本工作包修改文件、接口、迁移影响和验收ID，再写失败测试并实现。

保留Next.js/FastAPI与供应商兼容接口；采用本文选定的单主机数据库与独立Worker边界。
不要自行增加付费服务、通用Agent平台、任意代码执行或更多摄像头采集。
模型产物先校验，用户已确认状态受保护，所有新读写和订阅校验归属。

数据只在临时目录/副本测试；迁移先dry-run并记录来源hash，不能删除原JSON。
每个工作包提交可审核差异、实际测试结果与限制；不要预填“全部通过”。
若PRD与真实环境冲突，给出证据、影响和最小调整方案；保持其余工作推进。
所有目标完成后，按AC-01～42和发布门禁逐项出具报告，更新README和运行方式。
~~~

## 14. 参考资料与选型依据

以下官方资料用于模式与约束参考，不表示其全部能力已经接入本产品。框架能力不能替代本文的领域规则与验收。

- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：区分线程状态 checkpoint 与跨交互持久信息。
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)：规划、上下文整理与专家委派属于上层运行能力，可建立在 LangGraph 上。
- [Anthropic Multi-agent research system](https://www.anthropic.com/research/multi-agent-research-system)：主持协调、独立探索与结果综合的工程经验。
- [Building effective agents](https://www.anthropic.com/research/building-effective-agents)：组合工作流与有界评审修正模式。
- [Managed Agents](https://www.anthropic.com/research/managed-agents)：会话事件、Agent运行循环与执行环境的分离。
- [Temporal Durable AI](https://docs.temporal.io/ai)：长期任务与可恢复执行的可选基础设施方向。
- [SQLite WAL](https://sqlite.org/wal.html)、[SQLite Isolation](https://www.sqlite.org/isolation.html)：同主机、单写者、事务与WAL的实际约束。
- [SQLAlchemy Session Basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)：数据库Session与并发任务的边界。
- [Next.js安全公告示例](https://github.com/vercel/next.js/security/advisories/GHSA-h25m-26qc-wcjf)：依赖适用性核对与修复版本需逐项确认。

## 15. 完成定义

本版完成以可观察行为为准：产品定义在多轮中连续，任务可恢复且不重复应用结果，最新画布与产品状态一致，专家意见有依据或明确假设，审计能恢复和结束，迁移与权限不破坏用户数据，调用受预算限制，验收报告如实反映结果。

“增加了Agent类”“换了编排框架”“提示词更长”“有多个头像”“测试脚本打印PASS”均不能单独作为完成依据。
