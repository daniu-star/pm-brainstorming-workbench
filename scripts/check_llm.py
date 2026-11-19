"""模型服务连通性自检：一次最小调用定位「发消息失败」是否由模型配置引起。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/check_llm.py
    .venv/Scripts/python.exe scripts/check_llm.py --json

判定：
  * 200           → 模型可用（退出码 0）
  * 401 / 403     → API Key 无效/过期/无权限：改 backend/.env 的 LLM_API_KEY 后重启服务（退出码 2）
  * 402           → 账户余额不足（退出码 2）
  * 404           → 模型名或 base_url 配错（退出码 2）
  * 429           → 被限流（退出码 3，稍后重试即可）
  * 超时/连接失败 → 网络或代理问题（退出码 4）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def _mask(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return value[:2] + "***"
    return f"{value[:6]}***{value[-4:]} (len={len(value)})"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 LLM_API_KEY / base_url / model 是否可用")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，便于脚本化")
    parser.add_argument("--timeout", type=float, default=30.0, help="单次调用超时（秒）")
    args = parser.parse_args()

    os.environ.setdefault("ENV_FILE", str(BACKEND / ".env"))
    from core.config import settings  # noqa: E402  (依赖已注入的 ENV_FILE)

    info = {
        "base_url": settings.llm_base_url,
        "model": settings.llm_model,
        "api_key": _mask(settings.llm_api_key),
    }
    verdict: dict[str, object] = {"config": info, "status": "unknown", "http_status": None, "detail": ""}

    if not settings.llm_api_key:
        verdict.update(
            status="missing_key",
            detail="backend/.env 未配置 LLM_API_KEY；补齐后重启服务（stop.bat → start.bat）。",
        )
        code = 2
    else:
        import urllib.error
        import urllib.request

        url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 4,
            }
        ).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {settings.llm_api_key}")
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                payload = response.read().decode("utf-8", "replace")
                verdict.update(
                    status="ok",
                    http_status=response.status,
                    detail="模型可用：" + payload[:160],
                )
                code = 0
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            status = exc.code
            if status in (401, 403):
                verdict.update(
                    status="invalid_key",
                    http_status=status,
                    detail=(
                        "鉴权失败：API Key 无效、已过期或无权限。"
                        "请更新 backend/.env 的 LLM_API_KEY 后重启服务（stop.bat → start.bat）。"
                        f"\n上游响应：{raw[:300]}"
                    ),
                )
                code = 2
            elif status == 402:
                verdict.update(
                    status="insufficient_balance",
                    http_status=status,
                    detail=f"账户余额不足，请充值后重试。上游响应：{raw[:300]}",
                )
                code = 2
            elif status == 404:
                verdict.update(
                    status="bad_model_or_base_url",
                    http_status=status,
                    detail=(
                        "模型名或 base_url 配置错误（404）。"
                        f"当前 model={settings.llm_model} base_url={settings.llm_base_url}。"
                        f"上游响应：{raw[:300]}"
                    ),
                )
                code = 2
            elif status == 429:
                verdict.update(
                    status="rate_limited",
                    http_status=status,
                    detail=f"上游限流（429），稍后重试即可。上游响应：{raw[:300]}",
                )
                code = 3
            else:
                verdict.update(
                    status="http_error",
                    http_status=status,
                    detail=f"上游返回 HTTP {status}：{raw[:300]}",
                )
                code = 2
        except Exception as exc:  # noqa: BLE001 - 自检脚本需要兜住所有网络异常
            verdict.update(
                status="network_error",
                detail=(
                    f"无法连接上游（{type(exc).__name__}: {exc}）。"
                    "检查网络 / 代理，或确认 base_url 可访问。"
                ),
            )
            code = 4

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print("== 模型服务自检 ==")
        print(f"base_url : {info['base_url']}")
        print(f"model    : {info['model']}")
        print(f"api_key  : {info['api_key']}")
        print(f"结论     : {verdict['status']}")
        if verdict.get("http_status"):
            print(f"HTTP     : {verdict['http_status']}")
        print(f"说明     : {verdict['detail']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
