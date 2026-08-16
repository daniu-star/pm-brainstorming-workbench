"""第二轮遍历新增检查：C102-C109（checklist 第八节）。"""
from __future__ import annotations

from common import BACKEND, FRONTEND, check, read


def run() -> list[tuple[str, bool, str]]:
    print("\n== 第二轮遍历新增验证 ==")
    store = read(FRONTEND / "store" / "sessionStore.ts")

    load_src = store.split("loadSession: async")[1].split("const session = await")[0]
    check("C102", "abortStream()" in load_src and "resetStreamingBuffers()" in load_src,
          "切换会话先中断旧流")

    check("C103", 'partial + INTERRUPT_MARK' in store
          and store.count('INTERRUPT_MARK') >= 4,
          "失败 partial 统一落 [生成中断] 标记")

    hd = read(FRONTEND / "components" / "HistoryDrawer.tsx")
    gc = read(FRONTEND / "app" / "globals.css")
    check("C104", "touch-reveal" in hd and "touch-reveal" in gc
          and "pointer-events-none" in hd, "删除按钮触屏可达")

    ag = read(FRONTEND / "components" / "auth" / "AuthGate.tsx")
    check("C105", '"/product"' in ag, "/product 公开访问")

    example = read(BACKEND / ".env.example")
    check("C106", all(k in example for k in (
        "LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES", "AGENT_ROLE_INTERVAL",
        "STT_MAX_RECORDING_SECONDS")), ".env.example 字段齐全")

    voice = read(BACKEND / "core" / "voice.py")
    check("C107", "_stt_client" in voice and voice.count("AsyncOpenAI(") <= 2,
          "STT 客户端单例")

    prod = read(FRONTEND / "app" / "product" / "page.tsx")
    check("C108", "ChartIcon" not in prod and "scroll-mt-20" in prod,
          "product 页清理 + 锚点偏移")

    aloop = read(BACKEND / "core" / "agent_loop.py")
    check("C109", "return_exceptions=True" in aloop
          and "回复保存失败" in aloop, "持久化异常兜底 + done 保证")

    from common import _results
    return _results
