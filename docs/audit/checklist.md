# PM Brainstorm Workbench — Checklist（验证清单）

> 用于验证 buglist 中的修复是否完善。每条检查项对应 buglist 中的一个或多个 Bug ID。
> 状态：[ ] 待检查 / [x] 已通过 / [!] 未通过（需返工）
> 检查方式：A=自动化脚本 / M=手动验证 / R=代码审查

---

## 一、SMTP 工具链路验证

- [x] **C001** SMTP 配置完整（SMTP_HOST/PORT/USERNAME/PASSWORD/FROM 全部设置）— 检查方式：`python scripts/test_smtp.py --check-config` — 关联：无
- [x] **C002** AUTH_SECRET_KEY 已配置且长度 >= 32 — 检查方式：`python scripts/test_smtp.py --check-config` — 关联：无
- [x] **C003** 测试邮件实际发送成功 — 检查方式：`python scripts/test_smtp.py --send-test <email>` — 关联：无
- [x] **C004** /health 端点返回 status=ok — 检查方式：`python scripts/test_smtp.py --health` — 关联：无
- [x] **C005** SMTP 异常分类正确（认证失败/网络失败/协议失败/收件人拒绝 分别返回不同错误码与文案）— 检查方式：R — 关联：B032
- [x] **C006** 邮箱维度限流（60秒/邮箱 + 5次/小时）生效 — 检查方式：A — 关联：B035
- [x] **C007** IP 维度限流（10次/小时）生效 — 检查方式：A — 关联：B035
- [x] **C008** _verification_codes 字典定期清理，长期运行内存稳定 — 检查方式：A — 关联：B033
- [x] **C009** issue_verification_code 无 TOCTOU 竞态（并发请求不会绕过限流）— 检查方式：A — 关联：B034

## 二、后端安全验证

- [x] **C010** session_id 校验为 32 位 hex，路径穿越攻击被拒绝 — 检查方式：A — 关联：B002
- [x] **C011** 所有 session 路由校验 owner_email == caller_email，跨用户访问返回 403 — 检查方式：A — 关联：B001
- [x] **C012** list_sessions 仅返回当前用户会话 — 检查方式：A — 关联：B001
- [x] **C013** OPTIONS 请求被认证中间件放行，CORS 预检成功 — 检查方式：A — 关联：B003
- [x] **C014** CORS allow_methods 限定为 GET/POST/PUT/DELETE — 检查方式：R — 关联：B071
- [x] **C015** /health 不暴露服务配置详情（详情需管理员认证）— 检查方式：A — 关联：B070
- [x] **C016** canvas_routes.update_canvas 校验 tree 结构（Pydantic 模型）— 检查方式：R — 关联：B072
- [x] **C017** interview_routes 校验 session.phase，跨阶段调用返回 409 — 检查方式：A — 关联：B073

## 三、后端业务逻辑验证

- [x] **C018** RAG 索引在启动时构建，knowledge_base.json 存在且非空 — 检查方式：A — 关联：B004
- [x] **C019** rag_retriever.is_empty() 返回 False，brainstorm 注入 RAG 上下文 — 检查方式：A — 关联：B004
- [x] **C020** run_ask_all 四角色并行调用，互不污染上下文 — 检查方式：A — 关联：B029
- [x] **C021** LLM 流式调用异常时，已生成部分被持久化并发送 error 事件 — 检查方式：A — 关联：B030
- [x] **C022** llm_client 区分 APIRateLimitError/APITimeoutError/APIConnectionError — 检查方式：R — 关联：B031
- [x] **C023** llm_stream 跳过空 chunk.choices，不抛 IndexError — 检查方式：A — 关联：B063
- [x] **C024** session_store 并发写不丢更新（RLock 保护）— 检查方式：A — 关联：B027
- [x] **C025** session_store._save 原子写（临时文件 + os.replace）— 检查方式：R — 关联：B028
- [x] **C026** session_store 调用通过 asyncio.to_thread 不阻塞事件循环 — 检查方式：R — 关联：B038
- [x] **C027** 会话消息上限 200 条，超限自动截断 — 检查方式：A — 关联：B039
- [x] **C028** parse_conversation_to_tree 失败返回 None，不覆盖已有 Canvas — 检查方式：A — 关联：B036
- [x] **C029** parse_incremental 基于 canvas_last_msg_index 只传新消息 — 检查方式：R — 关联：B040
- [ ] **C030** 六维审计计数无双重递增，question_count 单次 respond 只 +1 — 检查方式：A — 关联：B037
- [ ] **C031** question_count >= 18 时强制结束面试 — 检查方式：A — 关联：B037
- [x] **C032** run_interview_respond 在更新 covered 后发送 dimensions_update SSE 事件 — 检查方式：A — 关联：B008
- [x] **C033** asyncio.sleep(0.3) 魔数已删除或可配置 — 检查方式：R — 关联：B080

## 四、前端 UI/UX 验证

- [x] **C034** RoleSelector @ 角色功能生效，InputBox 读取 targetRole 发送定向消息 — 检查方式：M — 关联：B005
- [x] **C035** 会话工作台 < md 单栏 Tab 切换，>= md 双栏 — 检查方式：M — 关联：B006
- [x] **C036** 摄像头前置告知与同意步骤，拒绝后可降级文字面试 — 检查方式：M — 关联：B007、B048
- [x] **C037** 审计进度由 SSE dimensions_update 驱动，6 格按真实 covered 点亮 — 检查方式：M — 关联：B008
- [x] **C038** 品牌主色统一（brand token），无 indigo-* 残留 — 检查方式：R — 关联：B009
- [x] **C039** 所有页面背景色统一（bg-base token）— 检查方式：R — 关联：B010
- [x] **C040** Header 高度跨页一致 — 检查方式：M — 关联：B011
- [x] **C041** app/not-found.tsx、app/error.tsx、app/loading.tsx 存在并沿用深色风格 — 检查方式：R — 关联：B012
- [x] **C042** HistoryDrawer 支持 Escape 关闭、焦点转移 — 检查方式：M — 关联：B013
- [x] **C043** 正文文字对比度 >= 4.5:1（无 text-zinc-600/700 用于可见文本）— 检查方式：R — 关联：B014
- [x] **C044** 消息容器有 aria-live="polite"，错误条有 role="alert" — 检查方式：R — 关联：B015
- [x] **C045** MessageList 仅在用户位于底部时自动滚动 — 检查方式：M — 关联：B016
- [x] **C046** CanvasPanel 空状态提供显式 CTA，不显示误导文案 — 检查方式：M — 关联：B017
- [x] **C047** SSE 断连提供"重试上一次发送"按钮 — 检查方式：M — 关联：B018
- [x] **C048** api.ts 错误消息友好（非 JSON 返回通用提示，detail 截断）— 检查方式：R — 关联：B019
- [x] **C049** Session.phase 与 SessionState.phase 类型统一 — 检查方式：R — 关联：B020
- [ ] **C050** ROLE_MAP 中 coach 和 interviewer 有独立 id — 检查方式：R — 关联：B021
- [ ] **C051** HistoryDrawer 使用 store 的 historySessions，无本地 state — 检查方式：R — 关联：B022
- [x] **C052** 所有 img 加 loading="lazy" — 检查方式：R — 关联：B023
- [ ] **C053** InterviewView TTS cleanup 被正确使用，卸载时停止音频 — 检查方式：M — 关联：B024
- [x] **C054** 登录页 sendCode 前客户端邮箱格式校验 — 检查方式：M — 关联：B025
- [x] **C055** 流式中点击"添加新对话"弹确认框 — 检查方式：M — 关联：B026
- [x] **C056** VoiceToggle 触控目标 >= 44px — 检查方式：R — 关联：B051
- [x] **C057** globals.css 滚动条宽度 >= 8px — 检查方式：R — 关联：B052
- [x] **C058** PipelineView 横向滚动有视觉提示 — 检查方式：M — 关联：B053
- [x] **C059** TreeRoot/TreeLeaf 死代码已删除或注释 — 检查方式：R — 关联：B054
- [x] **C060** skip-to-content 链接存在 — 检查方式：R — 关联：B055
- [x] **C061** AuthGate 缓存验证结果，路由切换不重复请求 — 检查方式：M — 关联：B057
- [x] **C062** CanvasToolbar 刷新按钮有 loading 态 — 检查方式：M — 关联：B090

## 五、视频面试验证

- [x] **C063** 监考数据（gaze + events）在结束面试时导出/上传 — 检查方式：M — 关联：B041
- [x] **C064** samples 数组有上限（ring buffer），30 分钟面试内存稳定 — 检查方式：A — 关联：B042
- [x] **C065** 视线偏离阈值上调（yaw=25°/pitch=20°/iris=0.18/awayEnterMs=3000ms）— 检查方式：R — 关联：B043
- [x] **C066** 显式校准步骤（3 秒、N>=20 样本），baseline 建立后冻结 — 检查方式：M — 关联：B044
- [x] **C067** getUserMedia 启用 echoCancellation/noiseSuppression/autoGainControl — 检查方式：R — 关联：B045
- [x] **C068** TTS 播放期间禁用麦克风 + 500ms 静默期 — 检查方式：M — 关联：B045
- [x] **C069** TTS 播放时高亮当前句子，失败时显示提示 — 检查方式：M — 关联：B046
- [x] **C070** 监考状态变化通过 aria-live 广播 — 检查方式：R — 关联：B047
- [x] **C071** 摄像头失败时提供"继续仅文字面试"降级路径 — 检查方式：M — 关联：B048
- [x] **C072** GPU 回退 CPU 时 UI 显示提示，CaptureInfo 含 delegate 字段 — 检查方式：M — 关联：B049
- [x] **C073** "连接稳定·24ms" 改为真实 RTT 测量 — 检查方式：M — 关联：B082
- [x] **C074** STT 录音上限可配置（>= 180s），接近上限倒计时提示 — 检查方式：M — 关联：B083
- [x] **C075** audio.play() AbortError 被处理，ObjectURL 不泄漏 — 检查方式：R — 关联：B084
- [x] **C076** face_missing/multi_face 恢复有 debounce，UI 不闪烁 — 检查方式：M — 关联：B085
- [x] **C077** exportGazeFile 输出含 meta.json 元数据 — 检查方式：R — 关联：B086
- [x] **C078** InterviewHeader 有"结束面试"按钮 — 检查方式：M — 关联：B087
- [x] **C079** video.onPlaying 不再提前 setStatus("active") — 检查方式：R — 关联：B088

## 六、工程化验证

- [x] **C080** next.config.js 中 typescript.ignoreBuildErrors = false — 检查方式：R — 关联：B050
- [x] **C081** tsc --noEmit 通过 — 检查方式：A — 关联：B050
- [x] **C082** 后端单元测试全部通过 — 检查方式：A — 关联：B075
- [x] **C083** test_session_store/test_agent_loop/test_interviewer/test_canvas_parser/test_retriever 存在 — 检查方式：A — 关联：B075
- [x] **C084** main.py 配置 logging.basicConfig — 检查方式：R — 关联：B074
- [x] **C085** 每个核心模块有 logger = logging.getLogger(__name__) — 检查方式：R — 关联：B074
- [x] **C086** SSE 事件构造统一使用 sse_event() 函数 — 检查方式：R — 关联：B076
- [x] **C087** canvas_parser JSON 提取统一使用 _extract_json() — 检查方式：R — 关联：B077
- [x] **C088** 全项目使用 list[X]/X | None 而非 typing.List/Optional — 检查方式：R — 关联：B078
- [x] **C089** config.py 使用 pydantic-settings BaseSettings — 检查方式：R — 关联：B079

---

## 验证脚本清单

以下脚本用于自动化验证上述检查项：

1. `scripts/test_smtp.py` — SMTP 工具链路验证（C001-C004）
2. `scripts/audit/check_security.py` — 后端安全验证（C010-C017）
3. `scripts/audit/check_backend_logic.py` — 后端业务逻辑验证（C018-C033）
4. `scripts/audit/check_frontend.py` — 前端 UI/UX 验证（C034-C062）
5. `scripts/audit/check_interview.py` — 视频面试验证（C063-C079）
6. `scripts/audit/check_engineering.py` — 工程化验证（C080-C089）
7. `scripts/audit/run_all.py` — 一键运行全部检查并汇总报告


## 七、第一轮遍历新增检查项

- [x] **C090** role_done 事件携带 content 与 role_name，前端 role_done 优先取 event.content — 检查方式：R — 关联：B110、B118
- [x] **C091** 前端按角色分流 token（roleBuffers），并行脑暴消息不混流 — 检查方式：R — 关联：B110
- [x] **C092** run_ask_all/run_agent_turn 入口将非 brainstorm 阶段归位并发 phase_change（skipCoach 不再卡死）— 检查方式：R — 关联：B111
- [x] **C093** 角色完成后先持久化再发 role_done；handleDone 兜底刷新画布 — 检查方式：R — 关联：B112
- [x] **C094** 空响应不落库（无裸 [生成中断] 消息）— 检查方式：R — 关联：B113
- [x] **C095** 单角色失败发 role_error（非终结），仅 done 解锁输入框 — 检查方式：R — 关联：B116
- [x] **C096** HistoryDrawer 提供删除会话入口（confirm + DELETE）— 检查方式：R — 关联：B115
- [x] **C097** 429 响应携带 retry_after，login 启动冷却 — 检查方式：R — 关联：B120
- [x] **C098** 旧 12 位会话文件已归档至 _archived/ — 检查方式：A — 关联：B114
- [x] **C099** 流式指示条显示中文角色名 — 检查方式：R — 关联：B118
- [!] **C100** handleError 不再 toast（单通道错误展示）— 检查方式：R — 关联：B119
- [x] **C101** store 无死代码（createSession 已删）；config 无死行；requirements 与实测环境对齐 — 检查方式：R — 关联：B121、B122、B123


## 八、第二轮遍历新增检查项

- [x] **C102** loadSession 切换会话前 abort 旧 SSE 流并清空流式状态 — 检查方式：R — 关联：B124
- [x] **C103** role_error 即时落带 [生成中断] 标记消息；单角色 error 分支 flush；handleDone 兜底带标记 — 检查方式：R — 关联：B125
- [x] **C104** 删除按钮触屏可达（touch-reveal）且 pointer-events 受控 — 检查方式：R — 关联：B126
- [x] **C105** /product 在认证白名单 — 检查方式：R — 关联：B127
- [x] **C106** .env.example 覆盖 config.py 全部可调字段 — 检查方式：R — 关联：B128
- [x] **C107** STT 客户端模块级单例 — 检查方式：R — 关联：B129
- [x] **C108** product 页无未用导入；锚点 scroll-mt — 检查方式：R — 关联：B130
- [x] **C109** run_role 持久化异常转 role_error；gather 保证 done — 检查方式：R — 关联：B131
