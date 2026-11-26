"""第 42 批测试：锁定 4 次尝试、未重试文案、重试进度通知。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
path = ROOT / "tests" / "api-retry.test.cjs"
src = path.read_text(encoding="utf-8")


def patch(old: str, new: str, *, count: int = 1) -> None:
    global src
    actual = src.count(old)
    assert actual == count, f"expected {count}, found {actual}:\n{old[:120]}"
    src = src.replace(old, new)


patch(
    " *  - GET 遇到 TypeError / 503 会自动重试（最多 3 次）；",
    " *  - GET 遇到 TypeError / 503 会自动重试（第 42 批起最多 4 次，首跳 12s 超时）；",
)

patch(
    '''  assert.equal(errorMessage(caught), api.NETWORK_ERROR_MESSAGE);
  assert.doesNotMatch(errorMessage(caught), /Failed to fetch/i);
  assert.equal(calls.length, 3, "GET 应尝试满 3 次才放弃");''',
    '''  assert.equal(errorMessage(caught), api.NETWORK_ERROR_MESSAGE);
  assert.doesNotMatch(errorMessage(caught), /Failed to fetch/i);
  assert.equal(calls.length, api.TRANSIENT_ATTEMPTS, "GET 应尝试满 TRANSIENT_ATTEMPTS 次才放弃");''',
)

src += '''
test("重试期间通过 subscribeRetryNotices 广播进度，恢复后广播 null", async () => {
  const notices = [];
  const unsubscribe = api.subscribeRetryNotices((notice) => notices.push(notice));
  try {
    installFetch((n) => {
      if (n < 4) throw new TypeError("Failed to fetch");
      return jsonResponse(200, { ok: true });
    });

    const result = await apiV2("/api/v2/quota");

    assert.deepEqual(result, { ok: true });
    assert.equal(calls.length, 4);
    const attempts = notices.filter(Boolean).map((notice) => notice.attempt);
    assert.deepEqual(attempts, [1, 2, 3], "每次重试前都要通知页面，用户才看得到「正在重试 2/4」");
    assert.ok(notices.some((notice) => notice && notice.path === "/api/v2/quota"));
    assert.equal(notices[notices.length - 1], null, "恢复后必须广播 null，让横幅自动收起");
  } finally {
    unsubscribe();
  }
});

test("重试全部失败时也会广播 null，避免横幅卡在「正在重试」", async () => {
  const notices = [];
  const unsubscribe = api.subscribeRetryNotices((notice) => notices.push(notice));
  try {
    installFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    await assert.rejects(() => apiV2("/api/v2/quota"));
    assert.equal(notices[notices.length - 1], null);
  } finally {
    unsubscribe();
  }
});

test("未重试的写请求使用「不会自动重放」文案，不谎称已重试", async () => {
  installFetch(() => {
    throw new TypeError("Failed to fetch");
  });

  let caught = null;
  try {
    await apiV2("/api/v2/llm-config", { method: "PUT", body: { api_key: "sk-x" } });
  } catch (error) {
    caught = error;
  }

  assert.ok(caught instanceof ApiError);
  assert.equal(caught.code, "NETWORK_ERROR");
  assert.equal(errorMessage(caught), api.NETWORK_ERROR_UNRETRIED_MESSAGE);
  assert.doesNotMatch(errorMessage(caught), /已自动重试/);
  assert.equal(calls.length, 1);
});

test("首跳超时早于网关 21 秒断点", () => {
  assert.ok(api.FIRST_ATTEMPT_TIMEOUT_MS <= 12_000);
  assert.ok(api.TRANSIENT_ATTEMPTS >= 4);
});
'''

path.write_text(src, encoding="utf-8", newline="")
print("patched tests/api-retry.test.cjs")
