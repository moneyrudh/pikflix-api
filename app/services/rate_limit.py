import asyncio


class RateLimiter:
    """Spaces calls at least 1/rate seconds apart across every request in this process."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1 / per_second
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:  # only booking a slot is serialized; the waiting isn't
            now = loop.time()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
        await asyncio.sleep(slot - now)
