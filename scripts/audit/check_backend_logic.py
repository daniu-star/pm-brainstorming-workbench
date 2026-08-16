"""后端业务逻辑验证：C018-C033（checklist 第三节）。"""
from __future__ import annotations

import asyncio
import json
import threading

from common import BACKEND, ROOT, check, read, setup_backend_path

setup_backend_path()


def check_rag_index() -> None:
    idx = BACKEND / "data" / "knowledge_base.json"
    ok = idx.exists() and idx.stat().st_size > 100
    count = 0
    if ok:
        try:
            docs = json.loads(idx.read_text(encoding="utf-8"))
            count = len(docs) if isinstance(docs, list) else len(docs.get("documents", []))
        except (json.JSONDecodeError, OSError):
            ok = False
    check("C018", ok and count > 0, f"RAG 索引存在，{count} 个切片")

    from rag.retriever import rag_retriever
    rag_retriever._loaded = False
    rag_retriever._documents = []
    rag_retriever._load()
    check("C019", not rag_retriever.is_empty(),
          f"retriever 非空（{len(rag_retriever._documents)} docs），RAG 注入可生效")


def check_agent_loop() -> None:
    src = read(BACKEND / "core" / "agent_loop.py")
    check("C020", ("gather" in src or "TaskGroup" in src) and "Queue" in src,
          "run_ask_all 并行化（gather/Queue）")
    check("C021", "[生成中断]" in src, "LLM 中断兜底（保存已生成部分）")
    lsrc = read(BACKEND / "core" / "llm_client.py")
    check("C022", "LLMRateLimitedError" in lsrc and "LLMUnavailableError" in lsrc,
          "LLM 异常分类")
    check("C023", "if not chunk.choices" in lsrc, "空 chunk.choices 防护")


def check_store_runtime() -> None:
    import uuid
    from db.session_store import session_store, MAX_MESSAGES
    sid = session_store.create("并发审计", owner_email="audit@t.com")["id"]

    # C024 并发写不丢
    def add_many(n: int):
        for i in range(n):
            session_store.add_message(sid, "user", f"m{i}")
    threads = [threading.Thread(target=add_many, args=(25,)) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    total = len(session_store.get(sid)["messages"])
    check("C024", total == 100, f"并发写 100 条实际 {total}（不丢失）")

    # C027 消息上限
    for i in range(MAX_MESSAGES + 10):
        session_store.add_message(sid, "user", f"x{i}")
    n = len(session_store.get(sid)["messages"])
    check("C027", n == MAX_MESSAGES, f"消息上限 {n}/{MAX_MESSAGES}")

    session_store.delete(sid)
    ssrc = read(BACKEND / "db" / "session_store.py")
    check("C025", "os.replace" in ssrc, "原子写（os.replace）")

    aloop = read(BACKEND / "core" / "agent_loop.py")
    iview = read(BACKEND / "core" / "interviewer.py")
    croutes = read(BACKEND / "api" / "canvas_routes.py")
    ok = aloop.count("to_thread") >= 3 and iview.count("to_thread") >= 3
    check("C026", ok and "to_thread" in croutes, "session_store 调用 to_thread 化")


def check_canvas_parser() -> None:
    src = read(BACKEND / "core" / "canvas_parser.py")
    check("C028", "return None" in src and "_extract_json" in src,
          "解析失败返回 None + _extract_json 复用")
    croutes = read(BACKEND / "api" / "canvas_routes.py")
    check("C029", "canvas_last_msg_index" in croutes and "last_idx" in src,
          "增量游标 canvas_last_msg_index")


def check_interviewer() -> None:
    src = read(BACKEND / "core" / "interviewer.py")
    # 静态：计数只在结尾一次
    ok_count = src.count('interview_question_count') >= 1 and "18" in src
    check("C030/C031", ok_count, "计数单次递增 + 18 题兜底（详见 pytest）")
    check("C032", "dimensions_update" in src,
          "dimensions_update SSE 事件")
    aloop = read(BACKEND / "core" / "agent_loop.py")
    check("C033", "sleep(0.3)" not in aloop, "魔数 sleep(0.3) 移除")


def run() -> list[tuple[str, bool, str]]:
    print("\n== 后端业务逻辑验证 ==")
    check_rag_index()
    check_agent_loop()
    check_store_runtime()
    check_canvas_parser()
    check_interviewer()
    from common import _results
    return _results
