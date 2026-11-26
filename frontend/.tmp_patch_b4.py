"""第 42 批（B4）：LLM 配置页加载三态（读取中先显示骨架，不渲染空表单）。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(".")
p = ROOT / "components/llm/LlmConfigForm.tsx"
src = p.read_text(encoding="utf-8")

old = "  if (loadError) {"
assert src.count(old) == 1
new = (
    "  // 第 42 批：读取期间以前直接渲染空表单，用户会以为配置丢了并重新填写/覆盖保存。\n"
    "  // 现在先出「正在读取配置」骨架，读完（成功或失败）才渲染表单。\n"
    "  if (view === null && !loadError) {\n"
    "    return (\n"
    '      <div role="status" className="flex flex-col gap-3 rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-4 text-sm text-zinc-300">\n'
    '        <p className="animate-pulse">正在读取模型配置…（后端唤醒时可能需要十几秒）</p>\n'
    '        <p className="text-xs text-zinc-500">\n'
    "          读取完成前不要重新填写：页面会用服务端已保存的配置覆盖表单，避免误覆盖旧 Key。\n"
    "        </p>\n"
    "      </div>\n"
    "    );\n"
    "  }\n"
    "\n"
    "  if (loadError) {"
)
src = src.replace(old, new)

# 读取中（重复读取）也禁用保存按钮：busy 已覆盖大部分，这里补 view===null 的兜底
old_btn = "          disabled={busy}\n          className=\"min-h-[44px] rounded-lg bg-brand-300/90 px-4 py-2 text-sm font-medium text-dark-900 transition-colors hover:bg-brand-300 disabled:cursor-not-allowed disabled:opacity-60\""
assert src.count(old_btn) == 1
src = src.replace(
    old_btn,
    "          disabled={busy || view === null}\n          className=\"min-h-[44px] rounded-lg bg-brand-300/90 px-4 py-2 text-sm font-medium text-dark-900 transition-colors hover:bg-brand-300 disabled:cursor-not-allowed disabled:opacity-60\"",
)

p.write_text(src, encoding="utf-8", newline="")
print("patched components/llm/LlmConfigForm.tsx")
