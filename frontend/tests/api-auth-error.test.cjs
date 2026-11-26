/**
 * `isUnauthenticated`（第 42 批）：401 / UNAUTHENTICATED 必须能与「网络故障」区分开。
 *
 * 背景：登录页曾出现「模型配置读取失败（网络不稳）」横幅——来源是未登录时
 * `GET /api/v2/llm-config` 返回 401，被闸门当成故障挂了出来。现在闸门用这个
 * 判定函数静默跳过（交给 AuthGate 跳登录页）。
 */
"use strict";

const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");

const BUILD_ROOT = path.join(process.cwd(), "node_modules", ".cache", "test-build");
const api = require(path.join(BUILD_ROOT, "lib", "api.js"));

test("401 / UNAUTHENTICATED 判定为未登录", () => {
  assert.equal(api.isUnauthenticated(new api.ApiError("UNAUTHENTICATED", "请先登录", 401, false)), true);
  assert.equal(api.isUnauthenticated(new api.ApiError("SOMETHING_ELSE", "服务暂不可用", 401, true)), true);
});

test("网络/超时/权限/普通 Error 都不算未登录", () => {
  assert.equal(api.isUnauthenticated(new api.ApiError("NETWORK_ERROR", "网络连接失败", 0, true)), false);
  assert.equal(api.isUnauthenticated(new api.ApiError("TIMEOUT", "请求超时", 0, true)), false);
  assert.equal(api.isUnauthenticated(new api.ApiError("FORBIDDEN", "没有权限", 403, false)), false);
  assert.equal(api.isUnauthenticated(new TypeError("Failed to fetch")), false);
  assert.equal(api.isUnauthenticated(null), false);
});
