"""SSE event construction utilities.

Provides a single `sse_event` function to build `data: ...\n\n` payloads
in a consistent, escaping-safe way across all streaming endpoints.
"""
import json


def sse_event(event_type: str, **kwargs) -> str:
    """Build a Server-Sent Events `data:` frame.

    The frame is always a JSON object containing a `type` field plus any
    additional keyword arguments. `ensure_ascii=False` keeps CJK text
    readable in logs and on the wire.

    Args:
        event_type: Logical event type (e.g. "token", "role_done", "error").
        **kwargs: Arbitrary payload fields merged into the event object.

    Returns:
        A string formatted as `data: {json}\n\n`, ready to be yielded by an
        async generator consumed by FastAPI's StreamingResponse.
    """
    payload = {"type": event_type, **kwargs}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
