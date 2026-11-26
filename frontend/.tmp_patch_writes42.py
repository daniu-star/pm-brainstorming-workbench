"""第 42 批（A）：写请求可安全重试 + 不再谎称已重试 + 错误文案统一走 errorMessage。"""
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


# ---------------------------------------------------------------- api.ts
patch(
    "lib/api.ts",
    [
        (
            "      const result = await apiOnce<T>(path, options);\n"
            "      if (attempt > 0) emitRetry(null);\n"
            "      return result;\n"
            "    } catch (error) {\n"
            "      lastError = normalizeFetchFailure(error, null);",
            "      const result = await apiOnce<T>(path, options, attempt > 0);\n"
            "      if (attempt > 0) emitRetry(null);\n"
            "      return result;\n"
            "    } catch (error) {\n"
            "      lastError = normalizeFetchFailure(error, null, attempt > 0);",
        ),
        (
            "function normalizeFetchFailure(error: unknown, requestId: string | null): unknown {\n"
            "  if (isApiError(error)) return error;\n"
            "  if (error instanceof Error && error.name === \"AbortError\") return error;\n"
            "  if (isNetworkFailure(error)) {\n"
            "    return new ApiError(\"NETWORK_ERROR\", NETWORK_ERROR_MESSAGE, 0, true, null, requestId, null);\n"
            "  }\n"
            "  return error;\n"
            "}",
            "function normalizeFetchFailure(\n"
            "  error: unknown,\n"
            "  requestId: string | null,\n"
            "  retried = false\n"
            "): unknown {\n"
            "  if (isApiError(error)) return error;\n"
            "  if (error instanceof Error && error.name === \"AbortError\") return error;\n"
            "  if (isNetworkFailure(error)) {\n"
            "    return new ApiError(\n"
            "      \"NETWORK_ERROR\",\n"
            "      retried ? NETWORK_ERROR_MESSAGE : NETWORK_ERROR_UNRETRIED_MESSAGE,\n"
            "      0,\n"
            "      true,\n"
            "      null,\n"
            "      requestId,\n"
            "      null\n"
            "    );\n"
            "  }\n"
            "  return error;\n"
            "}",
        ),
        (
            "async function apiOnce<T>(path: string, options?: RequestInit): Promise<T> {",
            "async function apiOnce<T>(\n"
            "  path: string,\n"
            "  options?: RequestInit,\n"
            "  retried = false\n"
            "): Promise<T> {",
        ),
        (
            "    if (guard.didTimeout()) {\n"
            "      throw new ApiError(\"TIMEOUT\", CODE_MESSAGES.TIMEOUT, 0, true, null, null, null);\n"
            "    }\n"
            "    throw normalizeFetchFailure(error, null);",
            "    if (guard.didTimeout()) {\n"
            "      throw new ApiError(\"TIMEOUT\", CODE_MESSAGES.TIMEOUT, 0, true, null, null, null);\n"
            "    }\n"
            "    throw normalizeFetchFailure(error, null, retried);",
        ),
    ],
)

# ---------------------------------------------------------------- 建会话迁到 v2（带幂等键与重试）
patch(
    "app/page.tsx",
    [
        (
            '      const session = await api<{ id: string }>("/api/session", {\n'
            '        method: "POST",\n'
            '        body: JSON.stringify({ problem_statement: problem.trim() }),\n'
            "      });",
            "      // 第 42 批：迁到 v2（自动带 Idempotency-Key + 瞬时故障重试），\n"
            "      // 否则网关抖动一次就会「建会话失败」，用户连产品都进不去。\n"
            "      const session = await createSession(problem.trim());",
        ),
    ],
)

# ---------------------------------------------------------------- 幂等写请求开启重试
patch(
    "lib/llmConfig.ts",
    [
        (
            '  return apiV2<LlmConfigView>("/api/v2/llm-config", { method: "PUT", body: input });',
            "  // PUT 是幂等覆盖写：重放不会产生第二条配置，可以安全重试。\n"
            "  return apiV2<LlmConfigView>(\"/api/v2/llm-config\", {\n"
            '    method: "PUT",\n'
            "    body: input,\n"
            "    retryTransient: true,\n"
            "  });",
        ),
        (
            '  return apiV2<LlmConfigView>("/api/v2/llm-config", { method: "DELETE" });',
            '  return apiV2<LlmConfigView>("/api/v2/llm-config", {\n'
            '    method: "DELETE",\n'
            "    retryTransient: true,\n"
            "  });",
        ),
    ],
)

# ---------------------------------------------------------------- 错误文案统一
patch(
    "components/loop/SandboxPanel.tsx",
    [
        (
            "        setStatusError(isApiError(err) ? err.message : errorMessage(err));",
            "        setStatusError(errorMessage(err));",
        ),
        (
            "      setFormError(isApiError(err) ? err.message : errorMessage(err));",
            "      setFormError(errorMessage(err));",
        ),
    ],
)

patch(
    "components/share/SharePanel.tsx",
    [
        (
            "      setFormError(isApiError(err) ? err.message : errorMessage(err));",
            "      setFormError(errorMessage(err));",
        ),
    ],
)

patch(
    "lib/access.ts",
    [
        (
            "  const message = error instanceof Error ? error.message : String(error);",
            "  const message = errorMessage(error);",
        ),
    ],
)

patch(
    "lib/teamGroups.ts",
    [
        (
            '  const message = error instanceof Error ? error.message : "";',
            "  const message = errorMessage(error);",
        ),
    ],
)

patch(
    "lib/loopView.ts",
    [
        ("      loadErrorText: input.loopError.message,", "      loadErrorText: errorMessage(input.loopError),"),
    ],
)

patch(
    "lib/auditView.ts",
    [
        (
            "    loadErrorText: showLoadError && error ? error.message || AUDIT_LOAD_ERROR_TEXT : null,",
            "    loadErrorText: showLoadError && error ? errorMessage(error) || AUDIT_LOAD_ERROR_TEXT : null,",
        ),
    ],
)

# ---------------------------------------------------------------- store：run.failed 文案脱敏
patch(
    "store/sessionStore.ts",
    [
        (
            "  function setError(message: string | null): void {",
            "  /**\n"
            "   * Run 失败文案：后端可能把供应商原始报错塞进 error_message（英文 401/超时），\n"
            "   * 直接显示会变成英文；这里优先用错误码映射，纯英文细节回落到中文兜底。\n"
            "   */\n"
            "  function runFailureMessage(code: string | null, message: string | null): string {\n"
            '    const detail = (message ?? "").trim();\n'
            "    const latinOnly = detail.length > 0 && !/[\\u4e00-\\u9fff]/.test(detail);\n"
            "    if (detail && !latinOnly) return detail;\n"
            "    const mapped = code ? errorMessage(new ApiError(code, \"\", 0)) : \"\";\n"
            "    if (mapped) return mapped;\n"
            '    return detail || "任务执行失败";\n'
            "  }\n\n"
            "  function setError(message: string | null): void {",
        ),
        (
            '        if (terminalStatus === "failed") {\n'
            "          setError(\n"
            '            typeof payload.error_message === "string" ? payload.error_message : "任务执行失败"\n'
            "          );",
            '        if (terminalStatus === "failed") {\n'
            "          setError(\n"
            "            runFailureMessage(\n"
            '              typeof payload.error_code === "string" ? payload.error_code : null,\n'
            '              typeof payload.error_message === "string" ? payload.error_message : null\n'
            "            )\n"
            "          );",
        ),
        (
            '          setError(run.error_message ?? "任务执行失败");',
            "          setError(runFailureMessage(run.error_code ?? null, run.error_message ?? null));",
        ),
    ],
)

print("done")
