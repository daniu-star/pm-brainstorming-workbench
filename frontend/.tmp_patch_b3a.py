"""第 42 批（B3）：AuthGate 超时放行 + 明确提示（按标记定位，避免中文注释匹配问题）。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(".")
p = ROOT / "components/auth/AuthGate.tsx"
src = p.read_text(encoding="utf-8")

# 1) 导入 errorMessage
old_import = 'import { api, ApiError } from "@/lib/api";'
assert src.count(old_import) == 1
src = src.replace(old_import, 'import { api, ApiError, errorMessage } from "@/lib/api";')

# 2) 给 /api/auth/me 加 5s 超时
old_call = '    api<{ authenticated: boolean }>("/api/auth/me")'
assert src.count(old_call) == 1
src = src.replace(
    old_call,
    "    // 第 42 批：旧实现走无超时的 legacy GET，Space 唤醒时最坏要转 43s；\n"
    "    // 5s 拿不到结果就先放行（页面自己的请求会再校验），避免冷启动白屏。\n"
    "    const controller = new AbortController();\n"
    "    const timer = setTimeout(() => controller.abort(), 5_000);\n"
    '    api<{ authenticated: boolean }>("/api/auth/me", { signal: controller.signal })',
)

# 3) 把「静默放行」换成「放行 + 可见提示 + 清定时器」
marker = "        } else {"
idx = src.index("const status = err instanceof ApiError ? err.status : 0;")
start = src.index(marker, idx)
tail_marker = "        }\n      });"
end = src.index(tail_marker, start) + len(tail_marker)
new_block = (
    "        } else {\n"
    "          // 网络/服务端问题：先放行，让页面自己的请求暴露真实错误；\n"
    "          // 但要给用户一条明确提示，否则他们不知道「为什么一进页面就报错」。\n"
    "          setAuthorized(true);\n"
    "          toast.error(\n"
    '            err instanceof Error && err.name === "AbortError"\n'
    '              ? "登录状态校验超时（网络不稳），已先按已登录继续；若提示未登录请重新登录。"\n'
    "              : `登录状态暂时无法校验：${errorMessage(err)}`\n"
    "          );\n"
    "        }\n"
    "      })\n"
    "      .finally(() => clearTimeout(timer));"
)
src = src[:start] + new_block + src[end:]
p.write_text(src, encoding="utf-8", newline="")
print("patched components/auth/AuthGate.tsx")
