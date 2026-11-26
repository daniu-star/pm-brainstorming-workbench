"""第 42 批（B1）：非 JSON 错误体解析修正（先 text 再 JSON.parse）。"""
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
    "lib/api.ts",
    [
        (
            "  if (!res.ok) {\n"
            "    let body: unknown = null;\n"
            "    try {\n"
            "      body = await res.json();\n"
            "    } catch {\n"
            "      try {\n"
            "        body = await res.text();\n"
            "      } catch {\n"
            "        body = null;\n"
            "      }\n"
            "    }\n"
            "    throw errorFromResponse(res.status, body);",
            "  if (!res.ok) {\n"
            "    // 第 42 批：必须先 text 再 JSON.parse。旧写法先 json() 再 text()，\n"
            "    // 网关返回 HTML 502 页面时 body 已被消费，text() 抛错被吞 → 丢掉 code/request_id。\n"
            "    let body: unknown = null;\n"
            "    let text = \"\";\n"
            "    try {\n"
            "      text = await res.text();\n"
            "    } catch {\n"
            "      text = \"\";\n"
            "    }\n"
            "    if (text) {\n"
            "      try {\n"
            "        body = JSON.parse(text);\n"
            "      } catch {\n"
            "        body = null;\n"
            "      }\n"
            "    }\n"
            "    throw errorFromResponse(res.status, body);",
        ),
    ],
)
print("done")
