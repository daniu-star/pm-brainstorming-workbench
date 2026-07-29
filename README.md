---
title: PM Brainstorm Workbench
emoji: 🧠
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# 产品脑暴工作台

面向产品经理的 AI 决策推演与审计工作台。

## 当前已实现

- 结构化需求澄清：逐项确认目标用户、替代方案、产品形态、成功指标与约束
- 独立多角色评审：CTO、设计师、运营和目标用户基于相同冻结上下文分别作答
- 冲突综合：汇总共识、分歧、证据缺口、隐含假设与待讨论事项
- 可追溯决策图谱：节点关联真实消息来源，包含版本和同步状态
- AI 专业语音审计：固定六维审计计划、进度恢复和最终报告
- 决策中心、Now/Next/Later 路线图、PRD 版本、团队评审、Agent 配置和指标复盘
- 团队账号中心：成员额度汇总、PRD 资产统计、子账号邀请、权限角色与实时聊天室
- BYOK、自带 API 地址与模型；SSE 流式响应；语音输入和 TTS
- 手机号登录与安全体验模式；体验身份由服务端签名并与其他用户数据隔离

审计通话是语音交互界面，不是 WebRTC 视频会议；产品也不宣称端到端加密。

## 技术与部署

- 前端：Next.js 16、Zustand、Tailwind CSS，部署在 Vercel
- 后端：FastAPI、SSE、OpenAI-compatible API、edge-tts，部署在 Hugging Face Space
- 生产站点：[www.brainstorming.top](https://www.brainstorming.top)

## 本地启动

1. 复制 `backend/.env.example` 为 `backend/.env`。
2. 配置 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL`，或在网页设置中使用 BYOK。
   本地没有短信服务时可使用体验模式；若需调试短信流程，可临时设置 `ALLOW_SMS_CODE_ECHO=true`。生产环境必须保持为 `false` 并配置真实短信服务。
   如需自动发送团队邀请邮件，请配置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`SMTP_FROM_EMAIL`；587 端口使用 `SMTP_USE_TLS=true`，465 端口使用 `SMTP_USE_SSL=true`。
3. 安装依赖：根目录执行 `npm ci`，后端执行 `pip install -r backend/requirements.txt`。
4. 启动后端：`uvicorn main:app --reload --port 8000`（工作目录为 `backend`）。
5. 启动前端：`npm run dev --workspace frontend`。

`.env` 已被 Git 忽略。不要提交真实 API Key；发现泄露后应立即在服务商后台撤销并重新生成。

## 质量检查

- 前端生产构建：`npm run build --workspace frontend`
- 后端测试：`pytest -q backend/tests`
- 后端语法检查：`python -m compileall -q backend`
- Pull Request 和 main 分支推送会自动执行构建、测试与密钥扫描。
