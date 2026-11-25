const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");

const build = path.join(__dirname, "..", "node_modules", ".cache", "test-build");

// 编译产物里保留了 `@/` 别名，这里按测试构建目录解析（与其它渲染测试同一套做法）。
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request.startsWith("@/")) {
    return originalResolve.call(this, path.join(build, request.slice(2)), ...rest);
  }
  return originalResolve.call(this, request, ...rest);
};

const { GATE_ALLOWED_PATHS, gateState } = require(path.join(build, "lib", "llmConfig.js"));

const view = (overrides) => ({
  mode: "byok",
  configured: false,
  requires_config: true,
  base_url: "https://api.deepseek.com/v1",
  model: "",
  key_hint: "",
  disable_reasoning: true,
  updated_at: null,
  last_verified_at: null,
  last_error: null,
  defaults: { base_url: "https://api.deepseek.com/v1", model: "deepseek-flash" },
  settings_path: "模型 API 设置",
  key_present: false,
  ...overrides,
});

test("白名单页面直接放行（登录页/介绍页/配置页本身）", () => {
  for (const p of ["/login", "/product", "/settings/llm"]) {
    assert.equal(gateState({ pathname: p, view: null, failed: false }), "bypass");
    assert.ok(GATE_ALLOWED_PATHS.includes(p));
  }
});

test("未拿到配置时显示加载态，不误判为需要配置", () => {
  assert.equal(gateState({ pathname: "/session/x", view: null, failed: false }), "loading");
});

test("需要配置时拦成 blocked", () => {
  assert.equal(
    gateState({ pathname: "/session/x", view: view({ requires_config: true }), failed: false }),
    "blocked"
  );
});

test("已配置时正常渲染业务页面", () => {
  assert.equal(
    gateState({
      pathname: "/session/x",
      view: view({ requires_config: false, configured: true }),
      failed: false,
    }),
    "ready"
  );
});

test("读配置失败时放行，避免一次抖动锁死应用", () => {
  assert.equal(gateState({ pathname: "/session/x", view: null, failed: true }), "bypass");
  assert.equal(
    gateState({ pathname: "/session/x", view: view({ requires_config: true }), failed: true }),
    "bypass"
  );
});
