"""A bus whose "app window" answers ``AppUiRequest`` events.

Stands in for the WebSocket forwarder plus the frontend agent
(``src/lib/appUi.ts``): every published event is recorded, and each
``AppUiRequest`` is answered through ``deliver_result`` on the next loop turn,
exactly as the ``POST /api/app-ui/result`` route would. ``answer=False`` models
no open window, so the request runs into its timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from jarvis.core.app_ui_bridge import deliver_result
from jarvis.core.events import AppUiRequest

Reply = dict[str, Any] | Callable[[AppUiRequest], dict[str, Any]]


class FakeAppWindowBus:
    def __init__(self, reply: Reply | None = None, *, answer: bool = True) -> None:
        self.events: list[Any] = []
        self.requests: list[AppUiRequest] = []
        self._reply: Reply = reply if reply is not None else {"ok": True, "result": {}}
        self._answer = answer
        #: Set once the first ``AppUiRequest`` was published.
        self.requested = asyncio.Event()

    async def publish(self, event: Any) -> None:
        self.events.append(event)
        if not isinstance(event, AppUiRequest):
            return
        self.requests.append(event)
        self.requested.set()
        if not self._answer:
            return
        payload = self._reply(event) if callable(self._reply) else self._reply
        asyncio.get_running_loop().call_soon(deliver_result, event.request_id, payload)
