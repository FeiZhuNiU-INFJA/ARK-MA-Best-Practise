"""进度队列与心跳。停止时等待在途 PATCH，避免晚到进度覆盖终态。"""
from __future__ import annotations

import asyncio
import time

from .protocol import stage_label


class ProgressReporter:
    def __init__(self, update, *, interval: float = 15):
        self.update = update
        self.interval = interval
        self.latest = ""
        self._wake = asyncio.Event()
        self._stopped = False
        self._last = 0.0
        self._task = asyncio.create_task(self._drive())

    async def feed(self, text: str) -> None:
        label = stage_label(text)
        if label:
            self.latest = label
            self._wake.set()

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        await self._task

    async def _drive(self) -> None:
        while not self._stopped:
            delay = max(0, self.interval - (time.monotonic() - self._last))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay or self.interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self._stopped:
                break
            if self._last and time.monotonic() - self._last < self.interval:
                continue
            self._last = time.monotonic()
            await self.update(self.latest)
