"""The pipeline's MCP client: sends notices through an MCP server's send_email tool.

The server is launched from an `mcpServers` config file, the same format
Claude Code reads (`claude --mcp-config mcp_config.json`), so the pipeline
and the AI tool share one server definition.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from dotenv import dotenv_values

TOOL_NAME = "send_email"
_CREDENTIAL_KEYS = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")


class McpSendError(RuntimeError):
    """The MCP server reported a failed send, or could not be reached."""


def load_server_config(config_path: Path, server_name: str = "gmail") -> tuple[StdioServerParameters, dict]:
    """Read one server entry; return launch parameters and resolved server env.

    A bare `python` command becomes this interpreter (so the server runs in the
    same virtualenv), and relative paths resolve against the config's folder.
    Gmail credentials come from the process environment, then .env beside
    the config, then a legacy config env block. Other process secrets are
    not copied into the server's environment.
    """
    config = json.loads(config_path.read_text())
    try:
        entry = config["mcpServers"][server_name]
    except KeyError:
        raise ValueError(f"{config_path} has no mcpServers.{server_name} entry") from None

    base = config_path.resolve().parent
    command = entry["command"]
    if command in ("python", "python3"):
        command = sys.executable
    elif "/" in command and not Path(command).is_absolute():
        command = str(base / command)
    env = dict(entry.get("env") or {})
    local_env = dotenv_values(base / ".env", interpolate=False)
    for name in _CREDENTIAL_KEYS:
        if name in os.environ:
            env[name] = os.environ[name]
        elif local_env.get(name) is not None:
            env[name] = local_env[name]
    return StdioServerParameters(command=command, args=entry.get("args", []), env=env, cwd=base), env


class McpEmailSender:
    """A `Sender` that calls the MCP server's send_email tool.

    Each send opens a short MCP session: start the server, call the tool,
    close. That costs about half a second per email, which is fine at the
    volumes Gmail allows, and keeps the code free of threads or shared state.
    Entering the `with` block checks once that the server starts and offers
    send_email, so a broken config fails before any notice is attempted.
    """

    def __init__(self, server: StdioServerParameters, timeout: float = 60.0):
        self._server = server
        self._timeout = timeout

    def __enter__(self) -> "McpEmailSender":
        try:
            tool_names = asyncio.run(asyncio.wait_for(self._list_tools(), self._timeout))
        except Exception as exc:
            raise McpSendError(f"could not start MCP server: {exc}") from exc
        if TOOL_NAME not in tool_names:
            raise McpSendError(f"could not start MCP server: no {TOOL_NAME} tool")
        return self

    def __exit__(self, *exc_info) -> None:
        pass

    def send(self, *, to: str, subject: str, body: str, headers: dict[str, str]) -> None:
        arguments = {"to": [to], "subject": subject, "body": body, "headers": headers}
        result = asyncio.run(asyncio.wait_for(self._call(arguments), self._timeout))
        if result.is_error:
            text = " ".join(getattr(block, "text", "") for block in result.content).strip()
            raise McpSendError(text or f"{TOOL_NAME} failed")

    async def _list_tools(self) -> set[str]:
        async with Client(self._server) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    async def _call(self, arguments: dict):
        async with Client(self._server) as client:
            return await client.call_tool(TOOL_NAME, arguments)
