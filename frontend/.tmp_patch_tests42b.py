"""第 42 批：EOF 重连耗尽改为轮询兜底，更新旧的「报错停止」断言并加轮询用例。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
path = ROOT / "tests" / "session-store.test.cjs"
src = path.read_text(encoding="utf-8")

OLD = '''test("事件流连续 EOF：重连次数递传，达到上限后报错并停止 streaming", async () => {
  prime();
  const calls = stubFetch((url) => {
    if (url.includes("/runs/") && url.includes("/events")) return {};
    return {
      id: "run_eof",
      session_id: "s1",
      action: "brainstorm",
      status: "running",
      error_code: null,
      error_message: null,
    };
  });

  useSessionStore.getState().subscribeRun("run_eof");
  assert.equal(useSessionStore.getState().isStreaming, true);

  await waitFor(() => {
    const state = useSessionStore.getState();
    return state.error !== null && state.isStreaming === false;
  }, 8000);

  const state = useSessionStore.getState();
  assert.match(state.error, /事件流已断开/);
  assert.equal(state.subscribingRunId, null);
  const runFetches = calls.filter(
    (call) => call.url.includes("/runs/run_eof") && !call.url.includes("/events")
  ).length;
  assert.ok(runFetches >= 1, "重连时必须先查 Run 是否已进入终态");
  assert.ok(runFetches <= 3, `重连次数应有上限，实际查询 ${runFetches} 次`);
});
'''

NEW = '''test("事件流连续 EOF：重连耗尽后切换轮询兜底，不把用户丢在错误页", async () => {
  prime();
  const calls = stubFetch((url) => {
    if (url.includes("/runs/") && url.includes("/events")) return {};
    return {
      id: "run_eof",
      session_id: "s1",
      action: "brainstorm",
      status: "running",
      error_code: null,
      error_message: null,
    };
  });

  useSessionStore.getState().subscribeRun("run_eof");
  assert.equal(useSessionStore.getState().isStreaming, true);

  // 第 42 批：SSE 重连 2 次仍失败时，不再 setError「事件流已断开，请刷新页面继续」，
  // 而是切到轮询（后端任务仍在跑，用户应该看到进度而不是一句「失败了」）。
  await waitFor(() => {
    const state = useSessionStore.getState();
    return state.stateWarnings.some((warning) => warning.includes("轮询")) && state.isStreaming === false;
  }, 12000);

  const state = useSessionStore.getState();
  assert.equal(state.error, null, "断线只是传输层问题，不能当成任务失败报错");
  assert.equal(state.subscribingRunId, null);

  const runFetches = () =>
    calls.filter((call) => call.url.includes("/runs/run_eof") && !call.url.includes("/events")).length;
  const before = runFetches();
  await waitFor(() => runFetches() > before, 8000);
  assert.ok(runFetches() >= 2, "轮询必须持续查询 Run 状态，直到任务进入终态");
  useSessionStore.getState().unsubscribe();
});
'''

assert src.count(OLD) == 1, src.count(OLD)
src = src.replace(OLD, NEW)
path.write_text(src, encoding="utf-8", newline="")
print("patched tests/session-store.test.cjs")
