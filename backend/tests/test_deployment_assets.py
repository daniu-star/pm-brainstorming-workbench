"""部署链路的契约测试（第 40 批）。

线上形态是「Vercel 前端 + Hugging Face Space(Docker) 后端」，本地是 start.bat 三进程。
两者共用同一份代码，但部署相关的几个文件容易被后来的改动悄悄改坏
（例如把 CMD 改回 uvicorn、把 NEXT_PUBLIC_API_URL 改回硬编码地址），
这类问题在本地测试里完全看不见，只有部署后才暴露。

因此这里把「部署契约」写成断言：文件存在、关键片段在场、启动器可导入。
不测 Docker 构建本身（CI 里没有 docker daemon），只测真正会静默回退的配置。
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
CONTAINER_START = REPO_ROOT / "scripts" / "container_start.py"
NEXT_CONFIG = REPO_ROOT / "frontend" / "next.config.js"
VERCEL_JSON = REPO_ROOT / "frontend" / "vercel.json"
FRONTEND_ENV_EXAMPLE = REPO_ROOT / "frontend" / ".env.example"
README = REPO_ROOT / "README.md"


class DeploymentAssetsTests(unittest.TestCase):
    """部署文件齐备且语义正确。"""

    def test_dockerfile_runs_container_start_on_port_7860(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        # HF Space 的 docker SDK 固定读 7860；启动器要同时拉起 Worker，不能只跑 uvicorn。
        self.assertIn('CMD ["python", "/app/scripts/container_start.py"', text)
        self.assertIn("EXPOSE 7860", text)
        self.assertIn("workers.run_worker", CONTAINER_START.read_text(encoding="utf-8"))
        # 镜像里必须自建 RAG 索引（backend/data 不进 git）。
        self.assertIn("from rag.index_builder import build_index", text)

    def test_dockerignore_keeps_backend_and_drops_frontend(self) -> None:
        lines = {
            line.strip()
            for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        # 前端与本地数据不该进镜像（镜像只跑后端）。
        self.assertIn("frontend", lines)
        self.assertIn("backend/data", lines)
        self.assertIn(".git", lines)
        # 反向保护：knowledge 语料必须留在构建上下文里（被 `*.md` 误伤过一次）。
        self.assertNotIn("**/*.md", lines)

    def test_next_config_rewrites_backend_prefix(self) -> None:
        text = NEXT_CONFIG.read_text(encoding="utf-8")
        self.assertIn('source: "/backend/:path*"', text)
        self.assertIn("BACKEND_ORIGIN", text)
        # 默认目标是本项目的 Space；被改成 localhost 会让线上全站 502。
        self.assertIn("hf.space", text)

    def test_vercel_json_points_frontend_at_backend_prefix(self) -> None:
        payload = json.loads(VERCEL_JSON.read_text(encoding="utf-8"))
        self.assertEqual(payload["framework"], "nextjs")
        self.assertEqual(payload["env"]["NEXT_PUBLIC_API_URL"], "/backend")

    def test_frontend_env_example_documents_both_modes(self) -> None:
        text = FRONTEND_ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8001", text)  # 本地：start.bat 的 API 端口
        self.assertIn("/backend", text)  # 线上：同源反代
        # 实际生效的（未注释的）那行必须是本地地址，否则本地开发会去连线上。
        active = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertEqual(active, ["NEXT_PUBLIC_API_URL=http://127.0.0.1:8001"])

    def test_readme_has_hugging_face_front_matter(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "README 首行必须是 front matter 分隔符")
        head = text.split("---\n", 2)[1]
        self.assertIn("sdk: docker", head)
        self.assertIn("app_port: 7860", head)

    def test_container_start_uses_stdlib_only_and_prepares_database(self) -> None:
        tree = ast.parse(CONTAINER_START.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        # 镜像里不再多装依赖：启动器必须是纯标准库。
        self.assertTrue(imported <= {"argparse", "os", "signal", "subprocess", "sys", "time", "__future__"}, imported)
        # Worker 不会自己建表，启动器必须补 initialize_database()。
        self.assertIn("initialize_database", CONTAINER_START.read_text(encoding="utf-8"))

    def test_container_start_missing_database_fails_fast(self) -> None:
        """建表失败要整体失败，不能留一个连不上表的半死容器。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 空的 app-dir：`from repositories.database import ...` 必然 ImportError。
            rc = subprocess.run(
                [
                    sys.executable,
                    str(CONTAINER_START),
                    "--port",
                    "8099",
                    "--no-worker",
                    "--app-dir",
                    tmp,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            ).returncode
        self.assertNotEqual(rc, 0)


class DeploymentEnvCompatTests(unittest.TestCase):
    """V3 → V4 的环境变量改名必须在代码里兼容。

    线上 Space 是 V3 时期建的，Variables 里很可能仍然只有 ``JWT_SECRET`` /
    ``SMTP_FROM_EMAIL``；如果 V4 只认新名字，升级后表现为「登录 500」「发件人为空」，
    而本地（backend/.env 里写的是新名字）完全复现不出来。
    """

    def test_auth_secret_key_accepts_legacy_jwt_secret(self) -> None:
        from core.config import Settings

        with mock.patch.dict(os.environ, {"JWT_SECRET": "legacy-secret-" + "x" * 32}, clear=False):
            os.environ.pop("AUTH_SECRET_KEY", None)
            fresh = Settings(_env_file=None)
        self.assertTrue(fresh.auth_secret_key.startswith("legacy-secret-"))

    def test_auth_secret_key_prefers_new_name(self) -> None:
        from core.config import Settings

        with mock.patch.dict(
            os.environ,
            {"AUTH_SECRET_KEY": "new-secret-" + "y" * 32, "JWT_SECRET": "legacy-secret"},
            clear=False,
        ):
            fresh = Settings(_env_file=None)
        self.assertTrue(fresh.auth_secret_key.startswith("new-secret-"))

    def test_smtp_from_accepts_legacy_smtp_from_email(self) -> None:
        from core.config import Settings

        os.environ.pop("SMTP_FROM", None)
        with mock.patch.dict(os.environ, {"SMTP_FROM_EMAIL": "sender@example.com"}, clear=False):
            fresh = Settings(_env_file=None)
        self.assertEqual(fresh.smtp_from, "sender@example.com")
class SpaceDeployScriptTests(unittest.TestCase):
    """Space 部署脚本契约：只推可部署子集（二进制会被 HF pre-receive 钩子拒收）。"""

    def setUp(self) -> None:
        self.script = REPO_ROOT / "scripts" / "deploy_hf_space.py"
        self.text = self.script.read_text(encoding="utf-8")

    def test_script_exists_and_uses_subset(self) -> None:
        self.assertTrue(self.script.is_file())
        # 子集必须覆盖镜像构建所需文件。
        for name in ("Dockerfile", ".dockerignore", "README.md", "backend", "scripts/container_start.py"):
            self.assertIn(f'"{name}"', self.text, name)
        self.assertIn("ls-tree", self.text)

    def test_script_pushes_force_to_space_main_branch(self) -> None:
        self.assertIn("space-main", self.text)
        self.assertIn("--force", self.text)
        # 不允许把前端目录带进 Space（wasm 模型是二进制）。
        self.assertNotIn('"frontend"', self.text)

    def test_script_builds_subset_tree_with_plumbing(self) -> None:
        # 用 read-tree + force-remove + write-tree 组装子集树：不建 worktree、
        # 不依赖临时目录写权限（桌面根与用户 TEMP 在部分机器上不可写）。
        for token in ("read-tree", "force-remove", "write-tree", "commit-tree", "update-ref"):
            self.assertIn(token, self.text)
        self.assertNotIn("worktree add", self.text)

    def test_script_removes_its_temp_index(self) -> None:
        self.assertIn("GIT_INDEX_FILE", self.text)
        self.assertIn("index_file.unlink()", self.text)


if __name__ == "__main__":
    unittest.main()
