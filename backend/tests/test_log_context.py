"""请求级日志上下文（第 44 批）：每条日志带 ``request_id``。

覆盖：
* 过滤器在未绑定请求时回落 ``-``，绑定后输出真实 id，重置后回到 ``-``；
* HTTP 中间件为没有请求号的请求生成一个，并回显 ``X-Request-ID`` 响应头；
* 调用方传入的 ``X-Request-ID`` 原样保留（不覆盖），且能出现在业务日志里；
* 未登录的 401 错误体带同一个 request_id（前端可展示、后端可 grep）；
* 连续两个请求的 request_id 不同（证明上下文不会串）。
"""
from __future__ import annotations

import logging
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import main
from core import log_context


class _Capture(logging.Handler):
    """把日志记录收集到列表，便于断言字段。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102 - Handler 接口
        self.records.append(record)


def _capture_logger() -> tuple[logging.Logger, _Capture]:
    logger = logging.getLogger("tests.rid")
    logger.setLevel(logging.INFO)
    handler = _Capture()
    handler.addFilter(log_context.RequestIdFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger, handler


class RequestIdFilterTests(unittest.TestCase):
    def test_unbound_request_logs_placeholder(self) -> None:
        logger, handler = _capture_logger()
        try:
            logger.info("worker tick")
            self.assertEqual(handler.records[-1].request_id, log_context.MISSING_REQUEST_ID)
        finally:
            logger.removeHandler(handler)

    def test_bind_and_reset_round_trip(self) -> None:
        logger, handler = _capture_logger()
        try:
            token = log_context.bind_request_id("req_abcdef123456")
            logger.info("inside")
            log_context.reset_request_id(token)
            logger.info("outside")
            self.assertEqual(handler.records[-2].request_id, "req_abcdef123456")
            self.assertEqual(handler.records[-1].request_id, log_context.MISSING_REQUEST_ID)
        finally:
            logger.removeHandler(handler)


class RequestIdMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        # 不进入 lifespan：这里只关心中间件，不触碰数据库。
        self.client = TestClient(main.app)

    def test_generated_request_id_is_echoed(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        request_id = response.headers.get("X-Request-ID", "")
        self.assertTrue(request_id.startswith("req_"), request_id)
        self.assertEqual(len(request_id), len("req_") + 12)

    def test_consecutive_requests_get_distinct_ids(self) -> None:
        first = self.client.get("/health").headers.get("X-Request-ID")
        second = self.client.get("/health").headers.get("X-Request-ID")
        self.assertTrue(first and second)
        self.assertNotEqual(first, second)

    def test_caller_supplied_request_id_is_preserved(self) -> None:
        response = self.client.get("/health", headers={"X-Request-ID": "req_manual_0001"})
        self.assertEqual(response.headers.get("X-Request-ID"), "req_manual_0001")

    def test_idempotency_key_is_used_as_request_id_when_header_absent(self) -> None:
        response = self.client.get("/health", headers={"Idempotency-Key": "idem_00012345"})
        self.assertEqual(response.headers.get("X-Request-ID"), "idem_00012345")

    def test_business_logs_carry_request_id_and_error_body_matches(self) -> None:
        logger, handler = _capture_logger()

        def _logging_verify(_cookie: str | None) -> None:
            logger.info("verify_session_token called")
            return None

        try:
            with mock.patch.object(main, "verify_session_token", _logging_verify):
                response = self.client.get(
                    "/api/v2/sessions", headers={"X-Request-ID": "req_trace_0001"}
                )
            self.assertEqual(response.status_code, 401)
            body = response.json()
            self.assertEqual(body["error"]["request_id"], "req_trace_0001")
            self.assertEqual(response.headers.get("X-Request-ID"), "req_trace_0001")
            self.assertTrue(handler.records)
            self.assertEqual(handler.records[-1].request_id, "req_trace_0001")
        finally:
            logger.removeHandler(handler)

    def test_unauthenticated_request_gets_generated_request_id_in_body(self) -> None:
        response = self.client.get("/api/v2/sessions")
        self.assertEqual(response.status_code, 401)
        body = response.json()
        header_id = response.headers.get("X-Request-ID", "")
        self.assertTrue(header_id.startswith("req_"))
        self.assertEqual(body["error"]["request_id"], header_id)


if __name__ == "__main__":
    unittest.main()
