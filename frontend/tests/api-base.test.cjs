const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const BUILD = path.join(__dirname, "..", "node_modules", ".cache", "test-build");
const API_JS = path.join(BUILD, "lib", "api.js");

/** 在给定的环境变量 / window 条件下重新加载 lib/api，读到它计算出的 API_BASE。 */
function loadApiBase({ configured, hostname, allowCross }) {
  delete require.cache[require.resolve(API_JS)];
  const prevEnv = process.env.NEXT_PUBLIC_API_URL;
  const prevAllow = process.env.NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API;
  const prevWindow = global.window;

  if (configured === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = configured;
  if (allowCross === undefined) delete process.env.NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API;
  else process.env.NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API = allowCross;
  if (hostname) global.window = { location: { hostname, origin: `https://${hostname}` } };
  else delete global.window;

  try {
    return require(API_JS).API_BASE;
  } finally {
    if (prevEnv === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = prevEnv;
    if (prevAllow === undefined) delete process.env.NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API;
    else process.env.NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API = prevAllow;
    if (prevWindow === undefined) delete global.window;
    else global.window = prevWindow;
    delete require.cache[require.resolve(API_JS)];
  }
}

const HF = "https://123chenjunliang-pm-brainstorm-workbench.hf.space";

test("第 42 批：生产页面 + 绝对基址 → 强制同源 /backend（否则 CORS 预检必拦）", () => {
  assert.equal(loadApiBase({ configured: HF, hostname: "www.brainstorming.top" }), "/backend");
});

test("第 42 批：本地页面 + 绝对基址 → 保留绝对地址（开发用 127.0.0.1:8001）", () => {
  assert.equal(
    loadApiBase({ configured: "http://127.0.0.1:8001", hostname: "127.0.0.1" }),
    "http://127.0.0.1:8001",
  );
});

test("第 42 批：配置成相对路径时原样使用", () => {
  assert.equal(loadApiBase({ configured: "/backend", hostname: "www.brainstorming.top" }), "/backend");
});

test("第 42 批：显式放开跨域时尊重配置", () => {
  assert.equal(
    loadApiBase({ configured: HF, hostname: "www.brainstorming.top", allowCross: "1" }),
    HF,
  );
});

test("第 42 批：未配置且无 window 时回退同源 /backend", () => {
  assert.equal(loadApiBase({ configured: undefined, hostname: undefined }), "/backend");
});
