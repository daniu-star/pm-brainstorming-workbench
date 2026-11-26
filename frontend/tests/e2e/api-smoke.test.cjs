/**
 * 跨进程后端 API 冒烟（PRD §11.1 的 `test:e2e`）。
 *
 * 为什么是「真跨进程」而不是 mock：M1 的核心风险不在单测覆盖的函数里，
 * 而在「真实 uvicorn 进程 + 真实鉴权中间件 + 真实路由挂载顺序」的组合行为——
 * 例如鉴权中间件在路由之前拦截、v2 路由是否真的挂上、旧 v1 适配器是否还在。
 * 这些只有启动一个真实进程才能验证。
 *
 * 只用 Node 内置模块（本机 npm registry 不可达，不允许新增依赖）：
 * node:child_process / node:http / node:net / node:fs / node:path / node:os /
 * node:test / node:assert。
 *
 * 运行：`npm run test:e2e`（即 `node --test tests/e2e/*.test.cjs`）
 *
 * 环境变量：
 * - `PYTHON_BIN`      后端解释器路径，默认 <repo>/.venv/Scripts/python.exe
 * - `E2E_BASE_URL`    兜底：指向一个已在运行的后端（如 http://127.0.0.1:8000），
 *                     此时不启动子进程，测试输出会明确标注 fallback。
 * - `CI`              超时/起不来时从「跳过」改为「失败」。
 *
 * 说明：后端 `core/config.py` 用 `load_dotenv(..., override=False)` 加载
 * backend/.env，所以这里注入的 `DATABASE_URL` 会覆盖 .env（环境变量优先），
 * 保证 E2E 用临时 SQLite 库，不污染 backend/data/app.db。
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { spawn, spawnSync, execFileSync } = require("node:child_process");
const http = require("node:http");
const net = require("node:net");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

// --------------------------------------------------------------------------- //
// 路径推导：本文件位于 <repo>/frontend/tests/e2e/
// --------------------------------------------------------------------------- //
const HERE = __dirname;
const FRONTEND_DIR = path.resolve(HERE, "../..");
const REPO_ROOT = path.resolve(FRONTEND_DIR, "..");
const BACKEND_DIR = path.join(REPO_ROOT, "backend");
const DEFAULT_PYTHON = path.join(REPO_ROOT, ".venv", "Scripts", "python.exe");

const READY_TIMEOUT_MS = 30_000;
const POLL_INTERVAL_MS = 50;
const STOP_TIMEOUT_MS = 5_000;

// --------------------------------------------------------------------------- //
// HTTP 小工具（http 模块，不依赖 fetch —— 便于拿到原始 status/text）
// --------------------------------------------------------------------------- //
function request(baseUrl, pathname, options = {}) {
  const target = new URL(pathname, baseUrl);
  const body = options.body === undefined ? undefined : JSON.stringify(options.body);
  const headers = { accept: "application/json", ...(options.headers || {}) };
  if (body !== undefined) headers["content-type"] = "application/json";

  return new Promise((resolve, reject) => {
    const req = http.request(
      target,
      { method: options.method || "GET", headers, timeout: 10_000 },
      (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          let json = null;
          try {
            json = text ? JSON.parse(text) : null;
          } catch {
            json = null;
          }
          resolve({ status: res.statusCode, headers: res.headers, text, json });
        });
      },
    );
    req.on("timeout", () => req.destroy(new Error(`请求超时：${target.href}`)));
    req.on("error", reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

/** 找一个空闲的本地端口（listen 0 后立刻释放）。 */
function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** 轮询 /health 直到 200 或超时。返回最后一条失败原因，便于 skip/fail 时说明。 */
async function waitForHealth(baseUrl, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "尚未发起请求";
  while (Date.now() < deadline) {
    try {
      const res = await request(baseUrl, "/health");
      if (res.status === 200) return { ok: true, lastError: null };
      lastError = `GET /health 返回 ${res.status}`;
    } catch (error) {
      lastError = error && error.code ? `${error.code}: ${error.message}` : String(error);
    }
    await sleep(POLL_INTERVAL_MS);
  }
  return { ok: false, lastError };
}

// --------------------------------------------------------------------------- //
// 子进程生命周期（Windows 上 kill 后必须等真正退出，否则临时库文件仍被锁）
// --------------------------------------------------------------------------- //
function waitForExit(child, timeoutMs) {
  if (!child || child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    let done = false;
    const finish = (exited) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      child.removeListener("exit", onExit);
      resolve(exited);
    };
    const onExit = () => finish(true);
    const timer = setTimeout(() => finish(false), timeoutMs);
    child.once("exit", onExit);
  });
}

async function stopChild(child) {
  if (!child || child.exitCode !== null) return;

  // 先温和终止；Windows 上 child.kill() 是 TerminateProcess，语义上足够。
  try {
    child.kill();
  } catch {
    /* 已经退出 */
  }
  if (await waitForExit(child, STOP_TIMEOUT_MS)) return;

  // 兜底：Windows 用 taskkill 强制杀进程树（uvicorn 若派生 worker 也能带走）
  if (process.platform === "win32" && child.pid) {
    try {
      execFileSync("taskkill", ["/pid", String(child.pid), "/f", "/t"], { stdio: "ignore" });
    } catch {
      /* 进程可能已消失 */
    }
  }
  await waitForExit(child, STOP_TIMEOUT_MS);
}

/** Windows 上 SQLite 文件句柄释放有延迟，删除临时目录需要重试。 */
function removeTempDir(dir) {
  if (!dir) return;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      fs.rmSync(dir, { recursive: true, force: true });
      return;
    } catch (error) {
      if (attempt === 4) {
        // 清理失败不应让「功能正确」的测试变红，但要留痕。
        console.error(`[e2e] 临时目录清理失败（${dir}）：${error.message}`);
        return;
      }
      // 同步重试前稍微等一等（用 Atomics.wait 做一个极短的阻塞 sleep）
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 100);
    }
  }
}

/**
 * 启动后端子进程。
 * 返回 { child, baseUrl, tmpDir }，baseUrl 形如 http://127.0.0.1:<port>。
 */
/**
 * 判断解释器是否真的能跑。
 *
 * 不能只用 fs.existsSync()：CI 里 PYTHON_BIN=python 是「裸命令名」，
 * existsSync("python") 永远 false（它不做 PATH 查找），于是 e2e 在 CI 上直接判失败。
 * 这里改为真跑一次 `--version`，裸命令名与绝对路径都能覆盖。
 */
function isPythonUsable(pythonBin) {
  if (!pythonBin) return false;
  if (pythonBin.includes("/") || pythonBin.includes("\\")) {
    return fs.existsSync(pythonBin);
  }
  try {
    const probe = spawnSync(pythonBin, ["--version"], { stdio: "ignore", timeout: 15_000 });
    return probe.status === 0;
  } catch {
    return false;
  }
}

async function startBackend() {
  const pythonBin = process.env.PYTHON_BIN || DEFAULT_PYTHON;
  if (!isPythonUsable(pythonBin)) {
    return {
      error: `未找到 Python 解释器：${pythonBin}（可用 PYTHON_BIN 指定，或用 E2E_BASE_URL 指向已运行的后端）`,
    };
  }

  const port = await findFreePort();
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pm-brainstorm-e2e-"));
  // SQLAlchemy 的 sqlite URL 在 Windows 上需要正斜杠 + 三斜杠前缀
  const dbPath = path.join(tmpDir, "e2e.db").replace(/\\/g, "/");
  const databaseUrl = `sqlite+pysqlite:///${dbPath}`;

  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: BACKEND_DIR,
    // 关掉 .env 里的真实外部依赖副作用：E2E 只验证 HTTP 契约，不调用模型/SMTP
    BACKEND_PORT: String(port),
  };

  const child = spawn(
    pythonBin,
    ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", String(port), "--no-access-log"],
    { cwd: BACKEND_DIR, env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true },
  );

  let stderr = "";
  let stdout = "";
  child.stderr?.on("data", (chunk) => {
    stderr += chunk.toString("utf8");
    if (stderr.length > 8_000) stderr = stderr.slice(-8_000);
  });
  child.stdout?.on("data", (chunk) => {
    stdout += chunk.toString("utf8");
    if (stdout.length > 4_000) stdout = stdout.slice(-4_000);
  });

  let spawnError = null;
  child.once("error", (error) => {
    spawnError = error;
  });

  const baseUrl = `http://127.0.0.1:${port}`;
  const health = await waitForHealth(baseUrl, READY_TIMEOUT_MS);

  if (!health.ok) {
    const detail = [
      `后端未在 ${READY_TIMEOUT_MS}ms 内就绪：${health.lastError}`,
      spawnError ? `spawn 错误：${spawnError.message}` : null,
      `命令：${pythonBin} -m uvicorn main:app --port ${port}（cwd=${BACKEND_DIR}）`,
      `DATABASE_URL=${databaseUrl}`,
      stdout ? `--- stdout ---\n${stdout.slice(-2_000)}` : null,
      stderr ? `--- stderr ---\n${stderr.slice(-4_000)}` : null,
    ]
      .filter(Boolean)
      .join("\n");
    await stopChild(child);
    removeTempDir(tmpDir);
    return { error: detail };
  }

  return { child, baseUrl, tmpDir, databaseUrl, port, stderrText: () => stderr };
}

// --------------------------------------------------------------------------- //
// 主测试
// --------------------------------------------------------------------------- //
test("后端 API 冒烟：真跨进程启动 uvicorn 并验证鉴权/路由/旧适配器", async (t) => {
  const fallbackBase = process.env.E2E_BASE_URL;
  let backend = null;
  let baseUrl = null;

  if (fallbackBase) {
    // 兜底路径：后端子进程起不来时，用已在运行的后端验证契约。
    baseUrl = fallbackBase.replace(/\/+$/, "");
    console.log(
      `[e2e] fallback：未启动子进程，改用 E2E_BASE_URL=${baseUrl}（请确认该后端是本仓库当前代码）`,
    );
    t.diagnostic(`fallback mode: E2E_BASE_URL=${baseUrl}`);
    const health = await request(baseUrl, "/health");
    assert.equal(health.status, 200, `fallback 后端 ${baseUrl} 的 /health 不可用`);
  }

  try {
    if (!baseUrl) {
      backend = await startBackend();
      if (backend.error) {
        // CI 上「起不来」必须是失败；本机允许 skip，避免把环境问题伪装成代码问题。
        if (process.env.CI) assert.fail(backend.error);
        console.error(`[e2e] 跳过：${backend.error}`);
        t.skip(`后端未能启动，已跳过（本机非 CI）：${backend.error.split("\n")[0]}`);
        return;
      }
      baseUrl = backend.baseUrl;
      console.log(`[e2e] 后端已就绪：${baseUrl}（临时库 ${backend.databaseUrl}）`);
      t.diagnostic(`spawned backend at ${baseUrl}, DB=${backend.databaseUrl}`);

      // 子进程 stderr 里出现 traceback 时留作诊断信息
      backend.stderrText = backend.stderrText || (() => "");
    }

    // ---- 断言 1：未登录访问 v2 端点 → 401 ----
    const sessions = await request(baseUrl, "/api/v2/sessions");
    assert.equal(sessions.status, 401, "GET /api/v2/sessions 未登录应返回 401");
    assert.equal(
      sessions.json?.error?.code,
      "UNAUTHENTICATED",
      "401 应使用统一错误体的 error.code=UNAUTHENTICATED",
    );
    // 旧前端兼容字段（frontend/lib/api.ts 会回退读 payload.detail）
    assert.equal(
      sessions.json?.detail,
      "请先使用邮箱登录",
      "401 应保留旧前端兼容的 detail 字段",
    );

    // ---- 断言 2：旧 v1 适配器仍在（不是 404）----
    // 该路径同样被鉴权中间件拦截（401），关键是不能 404 —— 404 意味着适配器被摘掉了。
    const legacy = await request(baseUrl, "/api/session", {
      method: "POST",
      body: { problem_statement: "e2e smoke: 旧 v1 适配器是否还在" },
    });
    assert.notEqual(legacy.status, 404, "POST /api/session 旧 v1 适配器不应是 404");
    assert.match(legacy.text, /"error"|"id"|"detail"/, "旧路径应返回 JSON（错误体或会话对象）");

    // ---- 断言 3：未知 v2 路径应暴露 404 语义 ----
    // 注意：鉴权中间件在路由匹配之前执行（backend/main.py:101-115），
    // 所以未登录时未知的 /api/** 路径会先得到 401。两者都证明「没有落到某个真实端点」。
    const unknownV2 = await request(baseUrl, "/api/v2/nope");
    assert.ok(
      unknownV2.status === 401 || unknownV2.status === 404,
      `GET /api/v2/nope 应为 401（鉴权前置）或 404，实际 ${unknownV2.status}`,
    );
    if (unknownV2.status === 401) {
      assert.equal(unknownV2.json?.error?.code, "UNAUTHENTICATED");
    } else {
      assert.equal(unknownV2.json?.error?.code, "NOT_FOUND");
    }
    t.diagnostic(`note: /api/v2/nope 未登录时为 ${unknownV2.status}（鉴权中间件先于路由匹配）`);

    // ---- 断言 4：公开前缀上的未知路径 → 真正的 404 ----
    // /api/auth 在中间件的 public 白名单内，因此能穿透到路由层，验证统一 404 错误体。
    const unknownPublic = await request(baseUrl, "/api/auth/nope");
    assert.equal(unknownPublic.status, 404, "GET /api/auth/nope 应返回 404");
    assert.equal(unknownPublic.json?.error?.code, "NOT_FOUND", "404 错误体 code 应为 NOT_FOUND");

    // ---- 断言 5：健康检查 ----
    const health = await request(baseUrl, "/health");
    assert.equal(health.status, 200);
  } finally {
    await stopChild(backend && backend.child);
    removeTempDir(backend && backend.tmpDir);
  }
});
