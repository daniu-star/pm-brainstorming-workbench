import asyncio
import json
from collections import defaultdict


class TeamEventBroker:
    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, team_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers[team_id].add(queue)
        return queue

    def unsubscribe(self, team_id: str, queue: asyncio.Queue) -> None:
        self._subscribers[team_id].discard(queue)
        if not self._subscribers[team_id]:
            self._subscribers.pop(team_id, None)

    def publish(self, team_id: str, event_type: str, payload: dict) -> None:
        event = json.dumps(
            {"type": event_type, "payload": payload},
            ensure_ascii=False,
        )
        for queue in tuple(self._subscribers.get(team_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    continue


team_event_broker = TeamEventBroker()
