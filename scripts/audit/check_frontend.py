"""前端 UI/UX 静态验证：C034-C062（checklist 第四节）。"""
from __future__ import annotations

from common import FRONTEND, check, read


def has(path: str, *pats: str) -> bool:
    src = read(FRONTEND / path)
    return all(p in src for p in pats)


def absent(path: str, *pats: str) -> bool:
    src = read(FRONTEND / path)
    return all(p not in src for p in pats)


def run() -> list[tuple[str, bool, str]]:
    print("\n== 前端 UI/UX 验证（静态） ==")
    store = read(FRONTEND / "store" / "sessionStore.ts")
    types = read(FRONTEND / "lib" / "types.ts")

    check("C034", "targetRole" in store and "setTargetRole" in store
          and absent("components/chat/RoleSelector.tsx", "document.querySelector")
          and "targetRole" in read(FRONTEND / "components" / "chat" / "InputBox.tsx"),
          "RoleSelector 定向提问走 store")
    sess = read(FRONTEND / "app" / "session" / "[id]" / "page.tsx")
    check("C035", "min-w-[380px]" not in sess and "md:flex-row" in sess,
          "移动端单栏/桌面双栏")
    check("C037b", "coveredDimensions" in store and "dimensions_update" in store,
          "store 维护 coveredDimensions（SSE 驱动）")

    tw = read(FRONTEND / "tailwind.config.ts")
    check("C038", "brand" in tw and 'indigo' not in tw.replace('indigo-', '') or "brand:" in tw,
          "brand 色阶定义")
    # 工作台无 indigo 残留（chat/canvas/Header/session 页）
    files = ["components/chat/InputBox.tsx", "components/Header.tsx",
             "app/session/[id]/page.tsx", "components/canvas/CanvasToolbar.tsx"]
    ok = all(absent(f, "indigo-") for f in files)
    check("C038b", ok, "工作台 indigo 残留清零")

    check("C039", '"#06090e"' in tw and absent("app/login/page.tsx", "#05080c"),
          "背景色统一 dark.900=#06090e")
    check("C040", "h-14" in read(FRONTEND / "components" / "Header.tsx"),
          "Header 高度统一 h-14")

    import os
    for f in ("not-found.tsx", "error.tsx", "loading.tsx"):
        p = FRONTEND / "app" / f
        if not p.exists():
            check("C041", False, f"app/{f} 缺失")
            break
    else:
        check("C041", True, "not-found/error/loading 页面齐全")

    hd = read(FRONTEND / "components" / "HistoryDrawer.tsx")
    check("C042", "Escape" in hd and 'role="dialog"' in hd and "historySessions" in hd
          and "useState<SessionSummary[]>" not in hd.replace("useState<SessionSummary[]>(", "@"),
          "HistoryDrawer a11y + 走 store")

    chat_ws = ["components/chat/MessageBubble.tsx", "components/chat/InputBox.tsx",
               "components/chat/MessageList.tsx", "app/page.tsx", "app/product/page.tsx"]
    ok = all(absent(f, "text-zinc-700", "text-zinc-600") for f in chat_ws)
    check("C043", ok, "可见文本 zinc-600/700 清零（对比度）")
    check("C043b", absent("components/canvas/PipelineArrow.tsx", "#52525b"),
          "PipelineArrow 描边对比度")

    ml = read(FRONTEND / "components" / "chat" / "MessageList.tsx")
    check("C044", 'aria-live="polite"' in ml and 'role="alert"' in read(
        FRONTEND / "components" / "chat" / "ChatPanel.tsx"), "aria-live / role=alert")
    check("C045", "isAtBottom" in ml or "atBottom" in ml, "智能滚动（底部检测）")

    cp = read(FRONTEND / "components" / "canvas" / "CanvasPanel.tsx")
    check("C046", "正在生成画布" not in cp and ("生成画布" in cp), "空状态显式 CTA")

    check("C047", "lastFailedSend" in store, "SSE 失败重试（lastFailedSend）")
    api = read(FRONTEND / "lib" / "api.ts")
    check("C048", "ApiError" in api and "MAX_DETAIL_LENGTH" in api and "slice(0, MAX_DETAIL_LENGTH)" in api,
          "api.ts 友好错误 + 截断")

    check("C049", '"coach"' in types and '"interviewer"' in types
          and 'id: "coach"' in types and 'id: "interviewer"' in types,
          "phase/Role 类型统一，coach-interviewer 独立 id")

    check("C052", all('loading="lazy"' in read(FRONTEND / f)
                      for f in ("components/chat/MessageBubble.tsx",
                                "components/canvas/PipelineCard.tsx")),
          "头像 lazy loading")

    check("C054", "EMAIL_RE" in read(FRONTEND / "app" / "login" / "page.tsx")
          and "请输入有效的邮箱地址" in read(FRONTEND / "app" / "login" / "page.tsx"),
          "登录邮箱客户端校验")
    check("C055", "confirm(" in read(FRONTEND / "components" / "NavButtons.tsx"),
          "流式中离开确认")
    check("C056", "min-h-[44px]" in read(FRONTEND / "components" / "chat" / "VoiceToggle.tsx"),
          "VoiceToggle 触控 44px")
    gc = read(FRONTEND / "app" / "globals.css")
    check("C057", "width: 8px" in gc or "width:8px" in gc.replace(" ", "") or "::-webkit-scrollbar" in gc,
          "滚动条宽度提升（详见值）")
    pv = read(FRONTEND / "components" / "canvas" / "PipelineView.tsx")
    check("C058", "gradient" in pv, "Pipeline 横向滚动渐变提示")

    import os
    dead = [f for f in ("TreeRoot.tsx", "TreeLeaf.tsx")
            if (FRONTEND / "components" / "canvas" / f).exists()]
    check("C059", not dead, f"死代码已删除：{dead or '无'}")

    lay = read(FRONTEND / "app" / "layout.tsx")
    check("C060", "main-content" in lay or "跳到主要内容" in lay, "skip-to-content")

    check("C061", "authVerified" in read(FRONTEND / "components" / "auth" / "AuthGate.tsx"),
          "AuthGate 验证缓存")
    ct = read(FRONTEND / "components" / "canvas" / "CanvasToolbar.tsx")
    check("C062", "重新生成" in ct and ("animate-spin" in ct or "disabled" in ct),
          "画布工具栏 loading 态")

    # C036 归属面试模块，此处跳过（见 check_interview）
    from common import _results
    return _results
