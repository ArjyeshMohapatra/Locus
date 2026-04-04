import asyncio
import threading
from typing import Any

_SUBSCRIBERS: set[asyncio.Queue] = set()
_SUBSCRIBERS_LOCK = threading.Lock()


def subscribe(maxsize: int = 100) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    with _SUBSCRIBERS_LOCK:
        _SUBSCRIBERS.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    with _SUBSCRIBERS_LOCK:
        _SUBSCRIBERS.discard(queue)


def publish(event: dict[str, Any]) -> None:
    with _SUBSCRIBERS_LOCK:
        subscribers = list(_SUBSCRIBERS)

    for queue in subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop the event for this subscriber to avoid blocking
            pass
