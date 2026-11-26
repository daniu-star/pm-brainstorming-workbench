"""第 42 批（A2）：补齐 shares/teamGroups 写请求重试 + page.tsx 导入。"""
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
    "app/page.tsx",
    [
        (
            'import { api } from "@/lib/api";',
            'import { createSession, errorMessage } from "@/lib/api";',
        ),
    ],
)

patch(
    "lib/shares.ts",
    [
        (
            "  return apiV2<SessionShare>(`/api/v2/sessions/${sessionId}/shares`, {\n"
            '    method: "POST",\n'
            "    body: input,\n"
            "  });",
            "  // 后端按 (session, email) upsert，重复提交只更新角色，可以安全重试。\n"
            "  return apiV2<SessionShare>(`/api/v2/sessions/${sessionId}/shares`, {\n"
            '    method: "POST",\n'
            "    body: input,\n"
            "    retryTransient: true,\n"
            "  });",
        ),
        (
            "    { method: \"DELETE\" }\n  );",
            "    { method: \"DELETE\", retryTransient: true }\n  );",
        ),
    ],
)
print("done")
