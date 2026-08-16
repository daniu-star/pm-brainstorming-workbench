"""第一轮遍历收尾：更新 buglist 状态、追加 B110-B123、checklist 追加检查项。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "audit"

# ---------- 1. buglist 状态更新 ----------
buglist = DOCS / "buglist.md"
text = buglist.read_text(encoding="utf-8")

# 全部 TODO → DONE（B108/B109 为合并重复项，单独标注）
text = text.replace("- **状态**：TODO", "- **状态**：DONE")
text = text.replace(
    "### B108 [后端/可观测] /health 端点公开暴露服务配置状态（与 B070 重复，合并）",
    "### B108 [后端/可观测] /health 端点公开暴露服务配置状态（与 B070 重复，合并）\n- **状态**：N/A（并入 B070）",
)
text = text.replace(
    "### B109 [前端/视觉] PipelineArrow stroke=\"#52525b\" 对比度不足（与 B014 合并）",
    "### B109 [前端/视觉] PipelineArrow stroke=\"#52525b\" 对比度不足（与 B014 合并）\n- **状态**：N/A（并入 B014）",
)
text = text.replace(
    "- **修复**：提供一次性迁移脚本（12 位→重写为 32 位 id + 补 `owner_email`，需产品决定归属），或启动时将旧文件移入 `data/sessions/_archived/` 并在 UI 提示。",
    "- **修复**：提供一次性迁移脚本（12 位→重写为 32 位 id + 补 `owner_email`，需产品决定归属），或启动时将旧文件移入 `data/sessions/_archived/` 并在 UI 提示。\n- **处理**：已执行 scripts/archive_old_sessions.py 归档 19 个旧文件；数据迁移/归属需产品决策（DEFER）。",
)

new_items = """

---

## 第一轮遍历新增（B110-B123，回归审查发现，2026-08-16）

### B110 [P0/集成回归] run_ask_all 并行化后 SSE token 交错，前端单缓冲拼接导致消息内容确定性错乱
- **文件**：backend/core/agent_loop.py、frontend/store/sessionStore.ts
- **修复**：role_done 事件携带全量 content + role_name；前端按角色分流 token（roleBuffers），role_done 优先取 event.content；handleDone 兜底残留缓冲。
- **状态**：DONE

### B111 [P1/集成回归] "跳过引导"后 phase 永久卡死 coach
- **文件**：backend/core/agent_loop.py
- **修复**：run_ask_all / run_agent_turn 入口检测 phase != brainstorm 时更新为 brainstorm 并发送 phase_change。
- **状态**：DONE

### B112 [P1/集成回归] 并行模式下画布增量缺失整轮更新
- **文件**：backend/core/agent_loop.py、frontend/store/sessionStore.ts
- **修复**：每个角色完成后先 add_message 持久化再发 role_done；前端 handleDone 追加 autoUpdateCanvas 兜底。
- **状态**：DONE

### B113 [P2] LLM 全失败时 4 条裸 [生成中断] 被持久化
- **修复**：空响应不落库（run_ask_all/run_agent_turn/run_coach 均加 if full_response 判断）。
- **状态**：DONE

### B114 [P2] 旧 12 位会话不可访问且无迁移
- **处理**：scripts/archive_old_sessions.py 已归档 19 个旧文件至 data/sessions/_archived/；数据迁移/归属需产品决策。
- **状态**：DONE（归档）/ DEFER（迁移）

### B115 [P2] 前端无删除会话入口
- **修复**：HistoryDrawer 每项增加删除按钮（confirm + DELETE + 刷新列表 + spinner）。
- **状态**：DONE

### B116 [P2] 并行脑暴部分失败时 error 过早解锁输入框
- **修复**：单角色失败改发非终结性 role_error 事件（仅提示不置 isStreaming=false）；终结语义保留给 done。
- **状态**：DONE

### B117 [P3] InterviewView 对 coveredDimensions 使用弱类型选择器
- **修复**：恢复强类型 useSessionStore((s) => s.coveredDimensions)。
- **状态**：DONE

### B118 [P3] 流式指示条显示英文 role id
- **修复**：role_start/role_done 统一携带 role_name；MessageList 用 ROLE_MAP 名称显示。
- **状态**：DONE

### B119 [P3] 同一错误 toast + 内联双通道重复播报
- **修复**：handleError 移除 toast.error，错误统一由 ChatPanel 内联展示（aria-live 由容器承担）。
- **状态**：DONE

### B120 [P3] 429 响应无 retry_after，前端不启动冷却
- **修复**：后端 429 detail 携带 {message, retry_after}；api.ts ApiError 增加 payload；login catch 429 后 setCooldown。
- **状态**：DONE

### B121 [P3] store.createSession 死代码
- **修复**：删除（首页直接调 api 建会话）。
- **状态**：DONE

### B122 [P3] requirements 锁 pydantic-settings==2.7.1 与实测环境漂移
- **修复**：对齐为实测通过的 pydantic-settings==2.13.1。
- **状态**：DONE

### B123 [P3] config.py 尾部死代码行 + session_store 重复读 env
- **修复**：删除死行；session_store 改从 settings.session_data_dir 读取（单一来源）。
- **状态**：DONE
"""

# 插入到"修复优先级建议"之前
marker = "\n## 修复优先级建议"
if marker in text:
    text = text.replace(marker, new_items + marker)
else:
    text += new_items

buglist.write_text(text, encoding="utf-8")
print("buglist.md 已更新")

# ---------- 2. checklist 追加 ----------
checklist = DOCS / "checklist.md"
cl = checklist.read_text(encoding="utf-8")
append = """

## 七、第一轮遍历新增检查项

- [ ] **C090** role_done 事件携带 content 与 role_name，前端 role_done 优先取 event.content — 检查方式：R — 关联：B110、B118
- [ ] **C091** 前端按角色分流 token（roleBuffers），并行脑暴消息不混流 — 检查方式：R — 关联：B110
- [ ] **C092** run_ask_all/run_agent_turn 入口将非 brainstorm 阶段归位并发 phase_change（skipCoach 不再卡死）— 检查方式：R — 关联：B111
- [ ] **C093** 角色完成后先持久化再发 role_done；handleDone 兜底刷新画布 — 检查方式：R — 关联：B112
- [ ] **C094** 空响应不落库（无裸 [生成中断] 消息）— 检查方式：R — 关联：B113
- [ ] **C095** 单角色失败发 role_error（非终结），仅 done 解锁输入框 — 检查方式：R — 关联：B116
- [ ] **C096** HistoryDrawer 提供删除会话入口（confirm + DELETE）— 检查方式：R — 关联：B115
- [ ] **C097** 429 响应携带 retry_after，login 启动冷却 — 检查方式：R — 关联：B120
- [ ] **C098** 旧 12 位会话文件已归档至 _archived/ — 检查方式：A — 关联：B114
- [ ] **C099** 流式指示条显示中文角色名 — 检查方式：R — 关联：B118
- [ ] **C100** handleError 不再 toast（单通道错误展示）— 检查方式：R — 关联：B119
- [ ] **C101** store 无死代码（createSession 已删）；config 无死行；requirements 与实测环境对齐 — 检查方式：R — 关联：B121、B122、B123
"""
cl += append
checklist.write_text(cl, encoding="utf-8")
print("checklist.md 已更新")
