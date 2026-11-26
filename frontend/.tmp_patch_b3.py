"""第 42 批（B3）：两道 Gate —— AuthGate 超时放行、LlmConfigGate 缓存 + 可见 bypass + 重试。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(".")


def patch(path: str, pairs: list[tuple[str, str]]) -> None:
    p = ROOT / path
    src = p.read_text(encoding="utf-8")
    for old, new in pairs:
        actual = src.count(old)
        assert actual == 1, f"{path}: expected 1 occurrence, found {actual}:\n{old[:160]}"
        src = src.replace(old, new)
    p.write_text(src, encoding="utf-8", newline="")
    print(f"patched {path}")


NEW_TAIL = (
    "        } else {\n"
    "          // 网络/服务端问题：先放行，让页面自己的请求暴露真实错误；\n"
    "          // 但要给用户一条明确提示，否则他们不知道「为什么一进页面就报错」。\n"
    "          setAuthorized(true);\n"
    "          toast.error(\n"
    "            err instanceof Error && err.name === \"AbortError\"\n"
    "              ? \"登录状态校验超时（网络不稳），已先按已登录继续；若提示未登录请重新登录。\"\n"
    "              : `登录状态暂时无法校验：${errorMessage(err)}`\n"
    "          );\n"
    "        }\n"
    "      })\n"
    "      .finally(() => clearTimeout(timer));"
)

patch(
    "components/auth/AuthGate.tsx",
    [
        (
            'import { api, ApiError } from "@/lib/api";',
            'import { api, ApiError, errorMessage } from "@/lib/api";',
        ),
        (
            "    let cancelled = false;\n    setAuthorized(false);\n"
            '    api<{ authenticated: boolean }>("/api/auth/me")',
            "    let cancelled = false;\n"
            "    setAuthorized(false);\n"
            "    // 第 42 批：旧实现走无超时的 legacy GET，Space 唤醒时最坏要转 43s；\n"
            "    // 5s 拿不到结果就先放行（页面自己的请求会再校验），避免冷启动白屏。\n"
            "    const controller = new AbortController();\n"
            "    const timer = setTimeout(() => controller.abort(), 5_000);\n"
            '    api<{ authenticated: boolean }>("/api/auth/me", { signal: controller.signal })',
        ),
        (
            "        } else {\n"
            "          // Network/server issue — let the page render; protected API calls\n"
            "          // will surface their own errors.\n"
            "          setAuthorized(true);\n"
            "        }\n"
            "      });",
            NEW_TAIL,
        ),
    ]
    if "// Network/server issue" in (ROOT / "components/auth/AuthGate.tsx").read_text(encoding="utf-8")
    else [
        (
            'import { api, ApiError } from "@/lib/api";',
            'import { api, ApiError, errorMessage } from "@/lib/api";',
        ),
        (
            "    let cancelled = false;\n    setAuthorized(false);\n"
            '    api<{ authenticated: boolean }>("/api/auth/me")',
            "    let cancelled = false;\n"
            "    setAuthorized(false);\n"
            "    // 第 42 批：旧实现走无超时的 legacy GET，Space 唤醒时最坏要转 43s；\n"
            "    // 5s 拿不到结果就先放行（页面自己的请求会再校验），避免冷启动白屏。\n"
            "    const controller = new AbortController();\n"
            "    const timer = setTimeout(() => controller.abort(), 5_000);\n"
            '    api<{ authenticated: boolean }>("/api/auth/me", { signal: controller.signal })',
        ),
        (
            "        } else {\n          setAuthorized(true);\n        }\n      });",
            NEW_TAIL,
        ),
    ],
)
print("done")
