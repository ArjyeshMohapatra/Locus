import asyncio
from typing import Any


_SUBSCRIBERS: set[asyncio.Queue] = set()


def subscribe(maxsize: int = 100) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _SUBSCRIBERS.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    _SUBSCRIBERS.discard(queue)


def publish(event: dict[str, Any]) -> None:
    for queue in _SUBSCRIBERS:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop the event for this subscriber to avoid blocking
            pass
