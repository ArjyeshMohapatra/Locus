import asyncio

from app import event_stream


def test_event_stream_publish_subscribe():
    async def _run():
        queue = event_stream.subscribe()
        try:
            payload = {"id": 1, "event_type": "created"}
            event_stream.publish(payload)
            received = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert received == payload
        finally:
            event_stream.unsubscribe(queue)

    asyncio.run(_run())
