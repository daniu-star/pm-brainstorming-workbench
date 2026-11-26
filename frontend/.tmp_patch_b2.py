"""第 42 批（B2）：SSE 首连把 5xx 也纳入重试，并给连接阶段加超时。"""
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


patch(
    "lib/sse.ts",
    [
        (
            "    const CONNECT_ATTEMPTS = 3;\n    const CONNECT_BACKOFF_MS = [1_200, 3_000];",
            "    const CONNECT_ATTEMPTS = 3;\n"
            "    const CONNECT_BACKOFF_MS = [1_200, 3_000];\n"
            "    // 网关在 Space 唤醒期会直接回 502/503/504 HTML，这类响应必须和网络异常一样重试；\n"
            "    // 连接阶段也要自己的超时，否则只能等网关 21s 断开才动（第 42 批）。\n"
            "    const CONNECT_TIMEOUT_MS = 10_000;\n"
            "    const RETRYABLE_STATUS = [502, 503, 504];",
        ),
        (
            "      try {\n"
            "        response = await fetchImpl(withAfterSeq(url, opts.afterSeq), {\n"
            "          method: \"GET\",\n"
            "          credentials: \"include\",\n"
            "          headers,\n"
            "          cache: \"no-store\",\n"
            "          signal: controller.signal,\n"
            "        });\n"
            "        break;\n"
            "      } catch (error) {\n"
            "        if (stopped || isAbortError(error)) {\n"
            "          close(\"abort\");\n"
            "          return;\n"
            "        }\n"
            "        lastError = error;\n"
            "      }",
            "      const timeoutController = new AbortController();\n"
            "      const timeoutTimer = setTimeout(() => timeoutController.abort(), CONNECT_TIMEOUT_MS);\n"
            "      const onExternalAbort = (): void => timeoutController.abort();\n"
            "      controller.signal.addEventListener(\"abort\", onExternalAbort, { once: true });\n"
            "      try {\n"
            "        response = await fetchImpl(withAfterSeq(url, opts.afterSeq), {\n"
            "          method: \"GET\",\n"
            "          credentials: \"include\",\n"
            "          headers,\n"
            "          cache: \"no-store\",\n"
            "          signal: timeoutController.signal,\n"
            "        });\n"
            "        if (RETRYABLE_STATUS.includes(response.status) && attempt + 1 < CONNECT_ATTEMPTS) {\n"
            "          lastError = new Error(`事件流连接失败（HTTP ${response.status}）`);\n"
            "          response = null;\n"
            "          continue;\n"
            "        }\n"
            "        break;\n"
            "      } catch (error) {\n"
            "        if (stopped || controller.signal.aborted || isAbortError(error)) {\n"
            "          close(\"abort\");\n"
            "          return;\n"
            "        }\n"
            "        lastError = error;\n"
            "      } finally {\n"
            "        clearTimeout(timeoutTimer);\n"
            "        controller.signal.removeEventListener(\"abort\", onExternalAbort);\n"
            "      }",
        ),
    ],
)
print("done")
