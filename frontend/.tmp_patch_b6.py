"""第 42 批（B6）：权限页三态 —— 失败时不再永远显示「正在读取权限信息…」。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(".")
p = ROOT / "app/settings/access/page.tsx"
src = p.read_text(encoding="utf-8")

# 1) 增加 loading 与刷新中状态
old = '  const [busyId, setBusyId] = useState<string | null>(null);'
assert src.count(old) == 1
src = src.replace(
    old,
    '  const [busyId, setBusyId] = useState<string | null>(null);\n'
    "  // 第 42 批：区分「首次加载」与「读取失败」，否则失败后三块内容永远停在「正在读取…」。\n"
    "  const [loading, setLoading] = useState(true);",
)

old = (
    "  const reload = useCallback(async () => {\n"
    "    setLoadError(null);\n"
    "    try {\n"
    "      setOverview(await getAccessOverview());\n"
    "    } catch (err) {\n"
    "      setOverview(null);\n"
    "      setLoadError(accessErrorHint(err));\n"
    "    }\n"
    "  }, []);"
)
assert src.count(old) == 1
src = src.replace(
    old,
    "  const reload = useCallback(async () => {\n"
    "    setLoadError(null);\n"
    "    setLoading(true);\n"
    "    try {\n"
    "      setOverview(await getAccessOverview());\n"
    "    } catch (err) {\n"
    "      setOverview(null);\n"
    "      setLoadError(accessErrorHint(err));\n"
    "    } finally {\n"
    "      setLoading(false);\n"
    "    }\n"
    "  }, []);",
)

# 2) 三处加载占位统一改成三态文案
old_placeholder = '        {overview === null ? (\n          <p className="text-xs text-zinc-500">正在读取权限信息…</p>'
new_placeholder = (
    "        {overview === null ? (\n"
)
assert src.count(old_placeholder) == 3
src = src.replace(old_placeholder, new_placeholder)

# 把紧随其后的 ) : ( ... 结构保留，仅替换提示文案：直接在渲染函数里改造 Section empty 文本
# 简化做法：在文件顶部加一个局部渲染器字符串，用于已有三处占位之后
old_text = '<p className="text-xs text-zinc-500">正在读取权限信息…</p>'
assert src.count(old_text) == 3
src = src.replace(
    old_text,
    "          <p className=\"text-xs text-zinc-500\">\n"
    "            {loading\n"
    "              ? \"正在读取权限信息…\"\n"
    "              : loadError\n"
    "                ? `权限信息读取失败：${loadError}`\n"
    "                : \"权限信息为空。\"}\n"
    "          </p>",
    1,
)

p.write_text(src, encoding="utf-8", newline="")
print("patched app/settings/access/page.tsx (step1)")
