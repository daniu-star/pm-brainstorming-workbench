"""第二轮遍历收尾：buglist 追加 B124-B131，checklist 追加 C102-C109。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "audit"

buglist = DOCS / "buglist.md"
text = buglist.read_text(encoding="utf-8")

round2 = """

---

## 第二轮遍历新增（B124-B131，2026-08-16）

### B124 [P1/集成回归] HistoryDrawer 切换会话不 abort 旧 SSE 流，跨会话消息串扰
- **修复**：loadSession 开头调用 abortStream() 并清空 error/lastFailedSend/targetRole/缓冲。
- **状态**：DONE

### B125 [P2/一致性] 角色失败时前端 partial 展示与后端落库不一致
- **修复**：role_error 即时把该角色缓冲落为 content+"[生成中断]" 消息（对齐后端 _persist_partial）；单角色 error 分支同样 flush 而非丢弃；handleDone 兜底 flush 加标记。
- **状态**：DONE

### B126 [P3] HistoryDrawer 删除按钮 opacity-0 触屏不可见但可误触
- **修复**：pointer-events-none + group-hover/focus-visible pointer-events-auto；globals.css 增加 @media (hover:none) .touch-reveal 强制可见。
- **状态**：DONE

### B127 [P3] /product 营销页被认证墙拦截
- **修复**：AuthGate PUBLIC_PATHS 加入 "/product"。
- **状态**：DONE

### B128 [P3] .env.example 缺少 config.py 新增字段
- **修复**：补 LLM_TIMEOUT_SECONDS/LLM_MAX_RETRIES/AGENT_ROLE_INTERVAL/STT_MAX_RECORDING_SECONDS。
- **状态**：DONE

### B129 [P3] transcribe_speech 每次新建 AsyncOpenAI 客户端
- **修复**：模块级懒加载单例 _stt_client。
- **状态**：DONE

### B130 [P3] product 页 unused imports + 锚点被 fixed header 遮挡
- **修复**：删除 ChartIcon/UserIcon 未用导入；#how-it-works 加 scroll-mt-20。
- **状态**：DONE

### B131 [P3] run_role 非预期异常导致 done 不发、前端静默结束
- **修复**：add_message 包 try/except 转 role_error；gather(return_exceptions=True) 保证 done 发出。
- **状态**：DONE
"""

text += round2
buglist.write_text(text, encoding="utf-8")

checklist = DOCS / "checklist.md"
cl = checklist.read_text(encoding="utf-8")
cl += """

## 八、第二轮遍历新增检查项

- [ ] **C102** loadSession 切换会话前 abort 旧 SSE 流并清空流式状态 — 检查方式：R — 关联：B124
- [ ] **C103** role_error 即时落带 [生成中断] 标记消息；单角色 error 分支 flush；handleDone 兜底带标记 — 检查方式：R — 关联：B125
- [ ] **C104** 删除按钮触屏可达（touch-reveal）且 pointer-events 受控 — 检查方式：R — 关联：B126
- [ ] **C105** /product 在认证白名单 — 检查方式：R — 关联：B127
- [ ] **C106** .env.example 覆盖 config.py 全部可调字段 — 检查方式：R — 关联：B128
- [ ] **C107** STT 客户端模块级单例 — 检查方式：R — 关联：B129
- [ ] **C108** product 页无未用导入；锚点 scroll-mt — 检查方式：R — 关联：B130
- [ ] **C109** run_role 持久化异常转 role_error；gather 保证 done — 检查方式：R — 关联：B131
"""
checklist.write_text(cl, encoding="utf-8")
print("第二轮文档已更新")
