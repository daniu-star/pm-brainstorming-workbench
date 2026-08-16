# PM Brainstorm 审计报告

生成时间：2026-08-16T17:46:38
结果：**105/105 通过**

## 明细

| 检查项 | 结果 | 说明 |
|---|---|---|
| C001-C004 | PASS | SMTP 配置/AUTH 密钥/health 模拟（exit=0） |
| C005 | PASS | SMTP 异常分类（认证/收件人/网络/SSL/协议） |
| C007 | PASS | IP 维度限流（10 次/小时） |
| C006 | PASS | 邮箱维度限流（重发冷却） |
| C008 | PASS | 验证码记录过期清理 |
| C009 | PASS | TOCTOU 并发发送次数=1（应为1） |
| C010 | PASS | session_id 路径穿越拒绝（32位hex 白名单） |
| C011 | PASS | 跨用户读取返回 403（应403） |
| C011b | PASS | 本人读取返回 200（应200） |
| C012 | PASS | 会话列表不包含他人会话 |
| C013 | PASS | OPTIONS 预检 status=200 |
| C014 | PASS | CORS 方法白名单 |
| C015 | PASS | 公开 health 无详情，详情需登录 |
| C016 | PASS | Canvas Pydantic 结构校验 |
| C017 | PASS | 脑暴阶段 respond 返回 409（应409） |
| C018 | PASS | RAG 索引存在，56 个切片 |
| C019 | PASS | retriever 非空（56 docs），RAG 注入可生效 |
| C020 | PASS | run_ask_all 并行化（gather/Queue） |
| C021 | PASS | LLM 中断兜底（保存已生成部分） |
| C022 | PASS | LLM 异常分类 |
| C023 | PASS | 空 chunk.choices 防护 |
| C024 | PASS | 并发写 100 条实际 100（不丢失） |
| C027 | PASS | 消息上限 200/200 |
| C025 | PASS | 原子写（os.replace） |
| C026 | PASS | session_store 调用 to_thread 化 |
| C028 | PASS | 解析失败返回 None + _extract_json 复用 |
| C029 | PASS | 增量游标 canvas_last_msg_index |
| C030/C031 | PASS | 计数单次递增 + 18 题兜底（详见 pytest） |
| C032 | PASS | dimensions_update SSE 事件 |
| C033 | PASS | 魔数 sleep(0.3) 移除 |
| C034 | PASS | RoleSelector 定向提问走 store |
| C035 | PASS | 移动端单栏/桌面双栏 |
| C037b | PASS | store 维护 coveredDimensions（SSE 驱动） |
| C038 | PASS | brand 色阶定义 |
| C038b | PASS | 工作台 indigo 残留清零 |
| C039 | PASS | 背景色统一 dark.900=#06090e |
| C040 | PASS | Header 高度统一 h-14 |
| C041 | PASS | not-found/error/loading 页面齐全 |
| C042 | PASS | HistoryDrawer a11y + 走 store |
| C043 | PASS | 可见文本 zinc-600/700 清零（对比度） |
| C043b | PASS | PipelineArrow 描边对比度 |
| C044 | PASS | aria-live / role=alert |
| C045 | PASS | 智能滚动（底部检测） |
| C046 | PASS | 空状态显式 CTA |
| C047 | PASS | SSE 失败重试（lastFailedSend） |
| C048 | PASS | api.ts 友好错误 + 截断 |
| C049 | PASS | phase/Role 类型统一，coach-interviewer 独立 id |
| C052 | PASS | 头像 lazy loading |
| C054 | PASS | 登录邮箱客户端校验 |
| C055 | PASS | 流式中离开确认 |
| C056 | PASS | VoiceToggle 触控 44px |
| C057 | PASS | 滚动条宽度提升（详见值） |
| C058 | PASS | Pipeline 横向滚动渐变提示 |
| C059 | PASS | 死代码已删除：无 |
| C060 | PASS | skip-to-content |
| C061 | PASS | AuthGate 验证缓存 |
| C062 | PASS | 画布工具栏 loading 态 |
| C036 | PASS | 隐私同意门 + 文字面试降级 |
| C063 | PASS | 结束面试 + gaze 数据导出 |
| C064 | PASS | samples ring buffer |
| C065 | PASS | 视线阈值放宽（yaw25/pitch20/3000ms） |
| C066 | PASS | 校准样本数>=20 + 中位数 baseline |
| C067 | PASS | getUserMedia AEC 约束 |
| C068 | PASS | TTS 播放期间禁用麦克风 |
| C069 | PASS | TTS 字幕高亮 + 失败提示 |
| C070 | PASS | 监考状态 aria 播报 + 错误 alert |
| C071 | PASS | 摄像头失败降级路径 |
| C072 | PASS | GPU/CPU 回退提示 |
| C073 | PASS | 真实 RTT 显示 |
| C074 | PASS | 录音上限可配置/提升 |
| C075 | PASS | play() AbortError 处理 |
| C076 | PASS | 状态恢复滞后防闪烁 |
| C077 | PASS | 导出文件含 _meta 元数据头 |
| C078 | PASS | 结束面试入口 |
| C079 | PASS | onPlaying 不再抢置 active |
| C080 | PASS | 构建不忽略 TS 错误 |
| C081 | PASS | tsc --noEmit（exit=0） |
| C082 | PASS | pytest（exit=0）47 passed in 10.47s |
| C083 | PASS | 核心测试文件缺失：无 |
| C084 | PASS | logging.basicConfig 配置 |
| C085 | PASS | 核心模块 logger 覆盖 |
| C086 | PASS | sse_event 统一构造 |
| C087 | PASS | _extract_json 复用 |
| C088 | PASS | typing.List/Optional 清零 |
| C089 | PASS | pydantic-settings 配置 |
| C090 | PASS | role_done 携带 content + role_name |
| C091 | PASS | 前端按角色分流 token + content 优先 |
| C092 | PASS | 非 brainstorm 阶段归位（skipCoach 不卡死） |
| C093 | PASS | 先持久化再 role_done + done 兜底刷画布 |
| C094 | PASS | 空响应不落库 |
| C095 | PASS | role_error 非终结语义 |
| C096 | PASS | HistoryDrawer 删除入口 |
| C097 | PASS | 429 retry_after + login 冷却 |
| C098 | PASS | 旧会话已归档（残留 0，_archived 存在=True） |
| C099 | PASS | 指示条中文角色名 |
| C100 | PASS | handleError 单通道（无 toast 调用） |
| C101 | PASS | 死代码清理 + 依赖对齐 |
| C102 | PASS | 切换会话先中断旧流 |
| C103 | PASS | 失败 partial 统一落 [生成中断] 标记 |
| C104 | PASS | 删除按钮触屏可达 |
| C105 | PASS | /product 公开访问 |
| C106 | PASS | .env.example 字段齐全 |
| C107 | PASS | STT 客户端单例 |
| C108 | PASS | product 页清理 + 锚点偏移 |
| C109 | PASS | 持久化异常兜底 + done 保证 |