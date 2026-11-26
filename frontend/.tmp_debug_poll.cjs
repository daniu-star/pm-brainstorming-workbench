const path = require("node:path");
const Module = require("node:module");

const BUILD_DIR = path.join(__dirname, "node_modules", ".cache", "test-build");
const STORE_PATH = path.join(BUILD_DIR, "store", "sessionStore.js");

const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, isMain, options) {
  if (request.startsWith("@/")) {
    return originalResolve.call(this, path.join(BUILD_DIR, request.slice(2)), parent, isMain, options);
  }
  return originalResolve.call(this, request, parent, isMain, options);
};

const { useSessionStore } = require(STORE_PATH);

useSessionStore.setState({
  sessionId: "s1",
  currentSessionId: "s1",
  legacyMode: false,
  phase: "interview",
  v2Phase: "audit_completed",
  sessionView: { id: "s1", session_version: 3, phase: "audit_completed", current_product_version: 2, current_audit_id: "a1", active_run_id: null, legacy_mode: false },
  isStreaming: false,
});

const calls = [];
global.fetch = async (url, init = {}) => {
  calls.push(String(url));
  const isEvents = String(url).includes("/events");
  const payload = isEvents
    ? {}
    : { id: "run_eof", session_id: "s1", action: "brainstorm", status: "running", error_code: null, error_message: null };
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
    headers: { get: () => (isEvents ? "text/event-stream" : "application/json") },
    body: isEvents ? null : undefined,
  };
};

useSessionStore.getState().subscribeRun("run_eof");

let tick = 0;
const timer = setInterval(() => {
  tick += 1;
  const state = useSessionStore.getState();
  console.log(
    `t=${(tick * 0.5).toFixed(1)}s streaming=${state.isStreaming} subscribing=${state.subscribingRunId} error=${JSON.stringify(state.error)} warnings=${JSON.stringify(state.stateWarnings)} calls=${calls.length} events=${calls.filter((u) => u.includes("/events")).length}`
  );
  if (tick > 60) {
    clearInterval(timer);
    process.exit(0);
  }
}, 500);
