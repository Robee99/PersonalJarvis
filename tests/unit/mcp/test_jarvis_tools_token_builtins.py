"""Tools credentials must also restrict the in-process MCP tools."""

from types import SimpleNamespace

import mcp.types as types
import pytest

from jarvis.intel import mcp_tools as intel
from jarvis.mcp import jarvis_tools_server as server
from jarvis.memory.brain_map import mcp_tools as brain_map
from jarvis.multimodal import tools as media


@pytest.mark.asyncio
async def test_scoped_credential_filters_builtin_listing_and_calls(monkeypatch):
    calls = []

    async def fake_call(name, _arguments):
        calls.append(name)
        return "fixture result", False

    for module in (brain_map, media, intel):
        monkeypatch.setattr(module, "call", fake_call)

    credential = SimpleNamespace(scope="tools", allowed_tools=["click", "world_globe"])
    context = server.TOOLS_TOKEN.set(credential)
    try:
        instance = server.build_server()
        listed = await instance.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
        names = {tool.name for tool in listed.root.tools}
        assert "world_globe" in names
        assert "project_summary" not in names
        assert "media-describe-image" not in names
        assert "world_incidents" not in names

        handler = instance.request_handlers[types.CallToolRequest]
        for name in ("project_summary", "media-describe-image", "world_incidents"):
            response = await handler(
                types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments={}))
            )
            assert response.root.isError
        assert calls == []

        response = await handler(
            types.CallToolRequest(
                params=types.CallToolRequestParams(
                    name="world_globe", arguments={"action": "reset"}
                )
            )
        )
        assert not response.root.isError
        assert calls == ["world_globe"]

        credential.allowed_tools = ["project_summary", "media-describe-image"]
        listed = await instance.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
        names = {tool.name for tool in listed.root.tools}
        assert "world_globe" not in names
        assert {"project_summary", "media-describe-image"} <= names
        response = await handler(
            types.CallToolRequest(
                params=types.CallToolRequestParams(
                    name="world_globe", arguments={"action": "reset"}
                )
            )
        )
        assert response.root.isError
        assert calls == ["world_globe"]
    finally:
        server.TOOLS_TOKEN.reset(context)
