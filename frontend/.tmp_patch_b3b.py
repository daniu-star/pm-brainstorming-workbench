"""第 42 批（B3b）：LlmConfigGate —— 读失败不再静默放行，给出可见提示 + 重试。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(".")
p = ROOT / "components/llm/LlmConfigGate.tsx"
src = p.read_text(encoding="utf-8")

old_import = (
    'import { GATE_ALLOWED_PATHS, gateState, getLlmConfig, type LlmConfigView } from "@/lib/llmConfig";'
)
assert src.count(old_import) == 1
src = src.replace(
    old_import,
    old_import + '\nimport { errorMessage } from "@/lib/api";',
)

old_state = (
    "  const [view, setView] = useState<LlmConfigView | null>(null);\n"
    "  const [failed, setFailed] = useState(false);"
)
assert src.count(old_state) == 1
src = src.replace(
    old_state,
    "  const [view, setView] = useState<LlmConfigView | null>(null);\n"
    "  const [failed, setFailed] = useState(false);\n"
    "  const [bypassNotice, setBypassNotice] = useState<string | null>(null);\n"
    "  const [retrying, setRetrying] = useState(false);",
)

old_effect = (
    "  useEffect(() => {\n"
    "    if (GATE_ALLOWED_PATHS.includes(pathname)) return;\n"
    "    let cancelled = false;\n"
    "    getLlmConfig()\n"
    "      .then((next) => {\n"
    "        if (!cancelled) setView(next);\n"
    "      })\n"
    "      .catch(() => {\n"
    "        if (!cancelled) setFailed(true);\n"
    "      });\n"
    "    return () => {\n"
    "      cancelled = true;\n"
    "    };\n"
    "  }, [pathname]);"
)
assert src.count(old_effect) == 1
new_effect = (
    "  useEffect(() => {\n"
    "    if (GATE_ALLOWED_PATHS.includes(pathname)) return;\n"
    "    let cancelled = false;\n"
    "    getLlmConfig()\n"
    "      .then((next) => {\n"
    "        if (cancelled) return;\n"
    "        setView(next);\n"
    "        setFailed(false);\n"
    "        setBypassNotice(null);\n"
    "      })\n"
    "      .catch((err) => {\n"
    "        if (cancelled) return;\n"
    "        // 读失败不能静默放行：必须告诉用户「应用可能不可用」并给出重试入口，\n"
    "        // 否则用户进入后每次操作都失败，却以为是自己配置错了（第 42 批）。\n"
    "        setFailed(true);\n"
    "        setBypassNotice(errorMessage(err));\n"
    "      });\n"
    "    return () => {\n"
    "      cancelled = true;\n"
    "    };\n"
    "  }, [pathname]);\n"
    "\n"
    "  const retry = (): void => {\n"
    "    if (retrying) return;\n"
    "    setRetrying(true);\n"
    "    setFailed(false);\n"
    "    setView(null);\n"
    "    getLlmConfig()\n"
    "      .then((next) => {\n"
    "        setView(next);\n"
    "        setBypassNotice(null);\n"
    "      })\n"
    "      .catch((err) => {\n"
    "        setFailed(true);\n"
    "        setBypassNotice(errorMessage(err));\n"
    "      })\n"
    "      .finally(() => setRetrying(false));\n"
    "  };"
)
src = src.replace(old_effect, new_effect)

old_render = '  if (state === "bypass" || state === "ready") return <>{children}</>;'
assert src.count(old_render) == 1
new_render = (
    '  if (state === "ready") return <>{children}</>;\n'
    "\n"
    '  if (state === "bypass") {\n'
    "    return (\n"
    "      <>\n"
    "        <div\n"
    '          role="status"\n'
    '          className="sticky top-0 z-40 flex flex-wrap items-center justify-center gap-2 bg-amber-500/15 px-4 py-2 text-center text-xs text-amber-200"\n'
    "        >\n"
    "          <span>\n"
    '            模型配置读取失败（网络不稳），功能可能不可用：{bypassNotice ?? "未知原因"}\n'
    "          </span>\n"
    "          <button\n"
    '            type="button"\n'
    "            onClick={retry}\n"
    '            className="min-h-[32px] rounded-md border border-amber-400/60 px-2 py-1 font-medium text-amber-100 hover:bg-amber-400/20"\n'
    "          >\n"
    '            {retrying ? "重试中…" : "重试读取"}\n'
    "          </button>\n"
    "        </div>\n"
    "        {children}\n"
    "      </>\n"
    "    );\n"
    "  }"
)
src = src.replace(old_render, new_render)

p.write_text(src, encoding="utf-8", newline="")
print("patched components/llm/LlmConfigGate.tsx")
