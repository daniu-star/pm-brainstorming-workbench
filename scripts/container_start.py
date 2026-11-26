"""容器内的进程编排：同一个镜像里同时拉起 API 与 Run Worker（第 40 批部署）。

为什么需要它：V4 的运行模型是「API 只落库、Worker 领取执行」（ADR-04），
本机靠 ``start.bat`` 起三个进程。Hugging Face Space 这类单容器平台只能跑一个
前台进程，因此用一个轻量启动器把两个 Python 进程托管起来：
Worker 先起（避免命令排队无消费者），API 继承前台。

设计约束：
* 只用标准库，避免给镜像增加依赖；
* 先建表再起 Worker：Worker 的 ``tick()`` 直接查 ``runs`` 表，不会自己 create_all，
  本机是靠 ``scripts/launch.ps1`` 单独跑一次 ``initialize_database()``——容器里没有
  这一步，所以启动器自己补上（否则日志里会一直刷 ``no such table: runs``）；
* 任一子进程退出就整体退出，交给平台的重启策略处理（不要留半死状态）；
* 收到 SIGTERM/SIGINT 时先让子进程优雅收尾，超时才强杀；
* 本地可跑（Windows 也可），便于在没有 Docker 的环境里验证容器行为。

用法::

    python scripts/container_start.py --port 7860
    python scripts/container_start.py --port 8099 --no-worker   # 只验 API
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

#: 收到终止信号后等待子进程退出的秒数；超时直接 kill -9。
SHUTDOWN_GRACE_SECONDS = 15.0


def _prepare_database(*, app_dir: str) -> int:
    """幂等建表（等价于 ``launch.ps1`` 里那一步），Worker 起来前必须完成。

    返回子进程退出码；非 0 表示建表失败（缺依赖/目录不可写），调用方应整体失败。
    """
    code = (
        "from repositories.database import initialize_database;"
        " initialize_database(); print('[container] db-ready', flush=True)"
    )
    print("[container] prepare database", flush=True)
    proc = subprocess.run([sys.executable, "-c", code], cwd=app_dir)
    return proc.returncode


def _spawn(module_args: list[str], *, cwd: str) -> subprocess.Popen:
    """以当前解释器拉起一个模块进程，stdout/stderr 直接继承（平台会收集日志）。"""
    cmd = [sys.executable, *module_args]
    print(f"[container] start {' '.join(module_args)}", flush=True)
    return subprocess.Popen(cmd, cwd=cwd)


def _terminate(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001 - 已退出/句柄失效都不该影响收尾
                pass
    deadline = time.time() + SHUTDOWN_GRACE_SECONDS
    for proc in procs:
        remaining = max(0.1, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 API + Run Worker")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT") or os.environ.get("BACKEND_PORT") or "7860"),
        help="API 监听端口（默认取 PORT / BACKEND_PORT，再回落 7860）",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("BACKEND_HOST", "0.0.0.0"),
        help="API 监听地址",
    )
    parser.add_argument("--no-worker", action="store_true", help="只起 API（调试用）")
    parser.add_argument(
        "--forwarded-allow-ips",
        default=os.environ.get("FORWARDED_ALLOW_IPS", "*"),
        help="信任哪些代理的 X-Forwarded-For（容器平台前置代理必须信任，否则限流按同一个 IP 计数）",
    )
    parser.add_argument(
        "--app-dir",
        default=os.getcwd(),
        help="Python 包根目录（镜像里就是 /app）",
    )
    args = parser.parse_args(argv)

    procs: list[subprocess.Popen] = []
    exit_code = 0

    def _handle_signal(signum, _frame):  # noqa: ANN001 - 信号处理签名固定
        print(f"[container] received signal {signum}, shutting down", flush=True)
        _terminate(procs)
        raise SystemExit(0)

    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError):  # 非主线程/平台不支持时忽略
                pass

    try:
        rc = _prepare_database(app_dir=args.app_dir)
        if rc != 0:
            print(f"[container] database init failed code={rc}", flush=True)
            return rc or 1
        if not args.no_worker:
            procs.append(_spawn(["-m", "workers.run_worker"], cwd=args.app_dir))
            # 让 Worker 先完成恢复，再开 API 端口，避免首请求踩空库。
            time.sleep(1.5)
        # --proxy-headers / --forwarded-allow-ips：容器平台（HF Space 等）在应用前面还有
        # 一层代理，不开这两项时 request.client.host 永远是代理地址，登录限流会退化为
        # 全站共享一个额度（任何人打满 10 次/小时，所有人都无法登录）。
        api = _spawn(
            [
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                args.host,
                "--port",
                str(args.port),
                "--proxy-headers",
                "--forwarded-allow-ips",
                args.forwarded_allow_ips,
            ],
            cwd=args.app_dir,
        )
        procs.append(api)

        while True:
            for proc in procs:
                code = proc.poll()
                if code is not None:
                    print(f"[container] child exit pid={proc.pid} code={code}", flush=True)
                    exit_code = code or 1
                    return exit_code
            time.sleep(1.0)
    finally:
        _terminate(procs)


if __name__ == "__main__":
    sys.exit(main())
