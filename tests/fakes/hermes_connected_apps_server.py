"""Loopback MCP fixture for tests run inside the separate native Hermes SDK."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import uvicorn
from fastapi import FastAPI

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import control_key, runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionDenied, ActionExecuted
from jarvis.core.protocols import ToolResult
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor
from jarvis.ui.web.mcp_server_routes import build_mcp_asgi_app


class FixtureConnector:
    name = "gmail"
    description = "Fixture mail: read or send to a local recorder only."
    risk_tier = "ask"
    schema = {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["read", "send"]}},
        "required": ["action"],
    }

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        return "safe" if args.get("action") == "read" else "ask"

    async def execute(self, args: dict[str, Any], _ctx: Any) -> ToolResult:
        return ToolResult(success=True, output={"fixture": args["action"]})


async def main(endpoint_file: Path, receipts_file: Path) -> None:
    bus = EventBus()

    async def record(event: Any) -> None:
        with receipts_file.open("a", encoding="utf-8") as target:
            target.write(
                json.dumps(
                    {
                        "event": type(event).__name__,
                        "success": getattr(event, "success", None),
                        "reason": getattr(event, "reason", None),
                    }
                )
                + "\n"
            )

    bus.subscribe(ActionExecuted, record)
    bus.subscribe(ActionDenied, record)
    executor = ToolExecutor(
        bus=bus, evaluator=RiskTierEvaluator(SafetyConfig()), approval=ApprovalWorkflow(bus)
    )
    runtime_refs.set_supervisor_tool_gateway(
        BrainSupervisorToolGateway(
            SimpleNamespace(_tools={"gmail": FixtureConnector()}, _tool_executor=executor)
        )
    )
    control_key.verify_control_key = lambda value: value == "fixture-control"
    app = FastAPI()
    app.mount("/api/control/mcp", build_mcp_asgi_app())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    started = asyncio.Event()

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            started.set()

    server = ReadyServer(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    await asyncio.wait_for(started.wait(), 3)
    assert server.started
    await asyncio.to_thread(
        endpoint_file.write_text,
        f"http://127.0.0.1:{sock.getsockname()[1]}/api/control/mcp/hermes",
        encoding="utf-8",
    )
    await task


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
