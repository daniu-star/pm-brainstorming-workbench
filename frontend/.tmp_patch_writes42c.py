"""第 42 批（A3）：补 errorMessage/ApiError/isTransientFailure 的导入。"""
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
    "lib/access.ts",
    [('import { apiV2 } from "@/lib/api";', 'import { apiV2, errorMessage } from "@/lib/api";')],
)

patch(
    "lib/teamGroups.ts",
    [('import { apiV2 } from "./api";', 'import { apiV2, errorMessage } from "./api";')],
)

patch(
    "lib/loopView.ts",
    [
        (
            "import {\n  CHECK_RESULTS,",
            'import { errorMessage } from "./api";\nimport {\n  CHECK_RESULTS,',
        ),
    ],
)

patch(
    "lib/auditView.ts",
    [
        (
            "import {\n  AUDIT_DIMENSION_LABELS,",
            'import { errorMessage } from "./api";\nimport {\n  AUDIT_DIMENSION_LABELS,',
        ),
    ],
)

patch(
    "store/sessionStore.ts",
    [
        (
            "  listSessionRuns,\n  newRequestId,",
            "  isTransientFailure,\n  listSessionRuns,\n  newRequestId,",
        ),
    ],
)
print("done")
