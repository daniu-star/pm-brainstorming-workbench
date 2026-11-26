"""第 42 批（B7）：errorMessage 兼容纯对象错误与网络文案白名单；groupErrorHint 保留自身兜底文案。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(".")

p = ROOT / "lib/api.ts"
src = p.read_text(encoding="utf-8")
old = (
    "export function errorMessage(error: unknown): string {\n"
    "  if (isApiError(error)) {\n"
    '    if (error.code === "NETWORK_ERROR") return error.message || NETWORK_ERROR_MESSAGE;\n'
    '    if (error.code === "TIMEOUT") return `${CODE_MESSAGES.TIMEOUT}（服务端连接超时，可能是后端正在唤醒）`;\n'
    "    return CODE_MESSAGES[error.code] ?? (error.message || statusMessage(error.status));\n"
    "  }\n"
    "  if (isNetworkFailure(error)) return NETWORK_ERROR_MESSAGE;\n"
    '  if (error instanceof Error && error.name === "AbortError") return "请求已取消";\n'
    "  if (error instanceof Error && error.message) return error.message;\n"
    '  return "请求失败，请稍后重试";\n'
    "}"
)
assert src.count(old) == 1
new = (
    "export function errorMessage(error: unknown): string {\n"
    "  if (isApiError(error)) {\n"
    '    if (error.code === "NETWORK_ERROR") {\n'
    "      // 只回放我们自己写下的两档网络文案，避免把上游英文原文透出给用户。\n"
    "      if (error.message === NETWORK_ERROR_MESSAGE || error.message === NETWORK_ERROR_UNRETRIED_MESSAGE) {\n"
    "        return error.message;\n"
    "      }\n"
    "      return NETWORK_ERROR_MESSAGE;\n"
    "    }\n"
    '    if (error.code === "TIMEOUT") return `${CODE_MESSAGES.TIMEOUT}（服务端连接超时，可能是后端正在唤醒）`;\n'
    "    return CODE_MESSAGES[error.code] ?? (error.message || statusMessage(error.status));\n"
    "  }\n"
    "  if (isNetworkFailure(error)) return NETWORK_ERROR_MESSAGE;\n"
    '  if (error instanceof Error && error.name === "AbortError") return "请求已取消";\n'
    "  if (error instanceof Error && error.message) return error.message;\n"
    "  // 纯对象错误（如视图层传入 { message, notFound }）也要能取到 message。\n"
    "  if (\n"
    "    typeof error === \"object\" &&\n"
    "    error !== null &&\n"
    '    typeof (error as { message?: unknown }).message === "string" &&\n'
    "    (error as { message: string }).message\n"
    "  ) {\n"
    "    return (error as { message: string }).message;\n"
    "  }\n"
    '  return "请求失败，请稍后重试";\n'
    "}"
)
src = src.replace(old, new)
p.write_text(src, encoding="utf-8", newline="")
print("patched lib/api.ts")

p = ROOT / "lib/teamGroups.ts"
src = p.read_text(encoding="utf-8")
old = (
    "export function groupErrorHint(error: unknown): string {\n"
    "  const message = errorMessage(error);\n"
    '  if (!message) return "操作失败，请稍后重试";'
)
assert src.count(old) == 1
new = (
    "export function groupErrorHint(error: unknown): string {\n"
    "  const message = errorMessage(error);\n"
    "  // 通用文案保持本页自己的措辞（「操作」而非「请求」），便于用户理解这是组操作失败。\n"
    '  const generic = message === "请求失败，请稍后重试" || message === "服务暂不可用，请稍后重试";\n'
    '  if (!message || generic) return "操作失败，请稍后重试";'
)
src = src.replace(old, new)
p.write_text(src, encoding="utf-8", newline="")
print("patched lib/teamGroups.ts")
