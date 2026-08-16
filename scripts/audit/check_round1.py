"""第一轮遍历新增检查：C090-C101（checklist 第七节）。"""
from __future__ import annotations

from common import BACKEND, FRONTEND, check, read


def run() -> list[tuple[str, bool, str]]:
    print("\n== 第一轮遍历新增验证 ==")
    aloop = read(BACKEND / "core" / "agent_loop.py")
    store = read(FRONTEND / "store" / "sessionStore.ts")

    check("C090", 'sse_event(\n            "role_done", role=role, role_name=display_name, content=full_response,'.replace("\n", "").replace("  ", "") in aloop.replace("\n", "").replace("  ", "").replace("    ", "") or
          ('content=full_response' in aloop and 'role_name=display_name' in aloop),
          "role_done 携带 content + role_name")

    check("C091", "roleBuffers" in store and "joinedStreamingContent" in store
          and "typeof event.content === \"string\"" in store,
          "前端按角色分流 token + content 优先")

    check("C092", 'phase_change", phase="brainstorm' in aloop.replace('"', '"')
          and aloop.count('{"phase": "brainstorm"}') >= 2,
          "非 brainstorm 阶段归位（skipCoach 不卡死）")

    check("C093", "# 先持久化再发 role_done" in aloop and "autoUpdateCanvas" in store.split("function handleDone")[1].split("function handleError")[0],
          "先持久化再 role_done + done 兜底刷画布")

    check("C094", aloop.count("if full_response:") >= 3,
          "空响应不落库")

    check("C095", '"role_error"' in aloop and '"role_error"' in store
          and "isStreaming: false" not in store.split('case "role_error"')[1].split("break;")[0],
          "role_error 非终结语义")

    hd = read(FRONTEND / "components" / "HistoryDrawer.tsx")
    check("C096", 'method: "DELETE"' in hd and "confirm" in hd, "HistoryDrawer 删除入口")

    auth_routes = read(BACKEND / "api" / "auth_routes.py")
    login = read(FRONTEND / "app" / "login" / "page.tsx")
    check("C097", "retry_after" in auth_routes and "429" in auth_routes
          and "ApiError" in login and "setCooldown(Math.ceil(retryAfter))" in login,
          "429 retry_after + login 冷却")

    import os
    leftovers = [f.name for f in (BACKEND / "data" / "sessions").glob("*.json")
                 if len(f.stem) == 12]
    archived = (BACKEND / "data" / "sessions" / "_archived").is_dir()
    check("C098", not leftovers and archived, f"旧会话已归档（残留 {len(leftovers)}，_archived 存在={archived}）")

    ml = read(FRONTEND / "components" / "chat" / "MessageList.tsx")
    check("C099", "roleDisplayName" in ml and "ROLE_MAP" in ml, "指示条中文角色名")

    err_handler = store.split("function handleError")[1].split("}\n")[0] if "function handleError" in store else ""
    check("C100", "toast.error" not in err_handler and "toast(" not in err_handler,
          "handleError 单通道（无 toast 调用）")

    config = read(BACKEND / "core" / "core.config.py" if False else BACKEND / "core" / "config.py")
    req = read(BACKEND / "requirements.txt")
    check("C101", "createSession: async" not in store
          and "兼容旧引用" not in config
          and "pydantic-settings==2.13.1" in req,
          "死代码清理 + 依赖对齐")

    from common import _results
    return _results
