"""The pipeline's MCP client: sends notices through an MCP server's send_email tool.

The server is launched from an `mcpServers` config file, the same format
Claude Code reads (`claude --mcp-config mcp_config.json`), so the pipeline
and the AI tool share one server definition.
"""

import asyncio
import json
import sys
import threading
from concurrent.futures import Future
from pathlib import Path

from mcp import Client, StdioServerParameters

TOOL_NAME = "send_email"


class McpSendError(RuntimeError):
    """The MCP server reported a failed send, or could not be reached."""


def load_server_config(config_path: Path, server_name: str = "gmail") -> tuple[StdioServerParameters, dict]:
    """Read one server entry; returns its launch parameters and its env block.

    A bare `python` command becomes this interpreter (so the server runs in the
    same virtualenv), and relative paths resolve against the config's folder.
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
    env = entry.get("env") or {}
    return StdioServerParameters(command=command, args=entry.get("args", []), env=env, cwd=base), env


class McpEmailSender:
    """Synchronous `Sender` backed by one MCP session for the whole run.

    The MCP client is async, and its session must be opened and closed by the
    same task. A background thread therefore runs an event loop with a single
    task that owns the session and serves send requests from a queue.
    """

    def __init__(self, server: StdioServerParameters, timeout: float = 60.0):
        self._server = server
        self._timeout = timeout
        self._ready: Future[None] = Future()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._requests: asyncio.Queue | None = None

    def __enter__(self) -> "McpEmailSender":
        self._thread = threading.Thread(target=asyncio.run, args=(self._serve(),), daemon=True)
        self._thread.start()
        try:
            self._ready.result(timeout=self._timeout)  # re-raises if the server failed to start
        except Exception as exc:
            raise McpSendError(f"could not start MCP server: {exc}") from exc
        return self

    def __exit__(self, *exc_info) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._requests.put_nowait, None)
        self._thread.join(timeout=self._timeout)

    def send(self, *, to: str, subject: str, body: str, headers: dict[str, str]) -> None:
        reply: Future = Future()
        arguments = {"to": [to], "subject": subject, "body": body, "headers": headers}
        self._loop.call_soon_threadsafe(self._requests.put_nowait, (arguments, reply))
        result = reply.result(timeout=self._timeout)
        if result.is_error:
            text = " ".join(getattr(block, "text", "") for block in result.content).strip()
            raise McpSendError(text or f"{TOOL_NAME} failed")

    async def _serve(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
            self._requests = asyncio.Queue()
            async with Client(self._server) as client:
                tools = await client.list_tools()
                if TOOL_NAME not in {tool.name for tool in tools.tools}:
                    raise McpSendError(f"server does not provide a {TOOL_NAME} tool")
                self._ready.set_result(None)
                while (request := await self._requests.get()) is not None:
                    arguments, reply = request
                    try:
                        reply.set_result(await client.call_tool(TOOL_NAME, arguments))
                    except Exception as exc:
                        reply.set_exception(exc)
        except Exception as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
