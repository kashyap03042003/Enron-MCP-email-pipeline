import asyncio
import json
import os
import smtplib
import ssl
import sys
import textwrap

import pytest
from mcp import Client

import mcp_server.gmail_server as gmail
from enron_pipeline.mcp_client import McpEmailSender, McpSendError, load_server_config


class FakeSMTP:
    """Stands in for smtplib.SMTP_SSL and records what would be sent."""
    sent: list = []
    contexts: list = []
    fail_login = False
    refuse: dict = {}  # address -> (code, message), as smtplib reports rejections

    def __init__(self, host, port, timeout, *, context=None):
        self.address = (host, port)
        FakeSMTP.contexts.append(context)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        self.credentials = (user, password)

    def send_message(self, msg):
        FakeSMTP.sent.append((self.address, self.credentials, msg))
        return FakeSMTP.refuse


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail_login, FakeSMTP.refuse = [], False, {}
    FakeSMTP.contexts = []
    monkeypatch.setattr(gmail.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    return FakeSMTP


def call(arguments: dict):
    async def run():
        async with Client(gmail.mcp) as client:
            return await client.call_tool("send_email", arguments)
    return asyncio.run(run())


def text_of(result) -> str:
    return " ".join(block.text for block in result.content)


# --- server, over the MCP protocol (in-process) -------------------------------

def test_server_lists_send_email_tool():
    async def run():
        async with Client(gmail.mcp) as client:
            return (await client.list_tools()).tools
    (tool,) = asyncio.run(run())
    assert tool.name == "send_email"
    assert set(tool.input_schema["required"]) == {"to", "subject", "body"}


def test_send_email_sends_through_gmail_smtp(smtp):
    result = call({"to": ["x@example.com"], "subject": "[Duplicate Notice] Re: Budget",
                   "body": "hello", "headers": {"References": "<1@x>", "X-Original-To": "a@enron.com"}})
    assert not result.is_error
    assert "Sent to x@example.com" in text_of(result)

    (address, credentials, msg), = smtp.sent
    assert address == ("smtp.gmail.com", 465)
    context, = smtp.contexts
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert credentials == ("me@gmail.com", "abcdefghijklmnop")  # spaces stripped
    assert msg["From"] == "me@gmail.com"
    assert msg["To"] == "x@example.com"
    assert msg["References"] == "<1@x>"
    assert msg["X-Original-To"] == "a@enron.com"
    assert msg.get_content() == "hello\n"


def test_certificate_verification_failure_is_reported_without_sending(smtp, monkeypatch):
    def reject_certificate(*args, **kwargs):
        raise ssl.SSLCertVerificationError("certificate verify failed")

    monkeypatch.setattr(gmail.smtplib, "SMTP_SSL", reject_certificate)
    result = call({"to": ["x@example.com"], "subject": "s", "body": "b"})
    assert result.is_error
    assert "SSLCertVerificationError" in text_of(result)
    assert smtp.sent == []


@pytest.mark.parametrize("arguments, message", [
    ({"headers": {"Bcc": "spy@x.com"}}, "header not allowed: Bcc"),
    ({"headers": {"X-Note": "a\r\nBcc: spy@x.com"}}, "contains a line break"),
    ({"subject": "hi\nBcc: spy@x.com"}, "subject contains a line break"),
    ({"to": ["not-an-address"]}, "invalid email address"),
    ({"to": []}, "at least one recipient"),
])
def test_invalid_requests_are_rejected_with_a_reason(smtp, arguments, message):
    result = call({"to": ["x@example.com"], "subject": "s", "body": "b", **arguments})
    assert result.is_error
    assert message in text_of(result)
    assert smtp.sent == []


def test_partially_refused_recipients_are_reported_not_hidden(smtp):
    smtp.refuse = {"bad@example.com": (550, b"No such user")}
    result = call({"to": ["good@example.com", "bad@example.com"], "subject": "s", "body": "b"})
    assert result.is_error
    assert "rejected bad@example.com" in text_of(result)
    assert "accepted good@example.com" in text_of(result)


def test_bad_app_password_gives_a_helpful_error(smtp):
    smtp.fail_login = True
    result = call({"to": ["x@example.com"], "subject": "s", "body": "b"})
    assert result.is_error and "App Password" in text_of(result)


def test_missing_credentials_are_reported(smtp, monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    result = call({"to": ["x@example.com"], "subject": "s", "body": "b"})
    assert result.is_error and "GMAIL_APP_PASSWORD is not set" in text_of(result)


# --- config loading ----------------------------------------------------------

def test_load_server_config(tmp_path):
    config = tmp_path / "mcp_config.json"
    config.write_text(json.dumps({"mcpServers": {"gmail": {
        "command": "python", "args": ["mcp_server/gmail_server.py"],
        "env": {"GMAIL_ADDRESS": "me@gmail.com", "GMAIL_APP_PASSWORD": "x"},
    }}}))
    params, env = load_server_config(config)
    assert params.command == sys.executable  # bare "python" -> this virtualenv
    assert params.args == ["mcp_server/gmail_server.py"]
    assert str(params.cwd) == str(tmp_path)
    assert env["GMAIL_ADDRESS"] == "me@gmail.com"


def test_relative_command_resolves_against_config_folder(tmp_path):
    config = tmp_path / "mcp_config.json"
    config.write_text(json.dumps({"mcpServers": {"gmail": {"command": ".venv/bin/python", "args": []}}}))
    params, _ = load_server_config(config)
    assert params.command == str(tmp_path / ".venv/bin/python")


def test_missing_server_entry(tmp_path):
    config = tmp_path / "mcp_config.json"
    config.write_text('{"mcpServers": {}}')
    with pytest.raises(ValueError, match="mcpServers.gmail"):
        load_server_config(config)


def test_example_config_is_valid_with_placeholder_credentials(tmp_path, monkeypatch):
    from enron_pipeline.config import PROJECT_ROOT
    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    # Copy to a temp dir to keep the developer's actual .env out of the test.
    config = tmp_path / "mcp_config.json"
    config.write_text((PROJECT_ROOT / "mcp_config.json.example").read_text())
    params, env = load_server_config(config)
    assert params.args == ["mcp_server/gmail_server.py"]
    assert env == {"GMAIL_ADDRESS": "your.name@gmail.com", "GMAIL_APP_PASSWORD": "xxxx xxxx xxxx xxxx"}


def test_real_dotenv_overrides_example_placeholders(tmp_path, monkeypatch):
    from enron_pipeline.config import PROJECT_ROOT
    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "mcp_config.json"
    config.write_text((PROJECT_ROOT / "mcp_config.json.example").read_text())
    (tmp_path / ".env").write_text('GMAIL_ADDRESS=me@gmail.com\nGMAIL_APP_PASSWORD="abcd efgh ijkl mnop"\n')
    _, env = load_server_config(config)
    assert env == {"GMAIL_ADDRESS": "me@gmail.com", "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop"}


def test_dotenv_credentials_resolve_beside_config(tmp_path, monkeypatch):
    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "mcp_config.json"
    config.write_text('{"mcpServers": {"gmail": {"command": "python"}}}')
    (tmp_path / ".env").write_text(
        'GMAIL_ADDRESS=local@example.com\nGMAIL_APP_PASSWORD="abcd efgh ijkl mnop"\n'
        'UNRELATED_SECRET=private\n'
    )
    params, env = load_server_config(config)
    assert env == {"GMAIL_ADDRESS": "local@example.com", "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop"}
    assert params.env == env
    assert "GMAIL_ADDRESS" not in os.environ


def test_environment_overrides_dotenv_and_legacy_config(tmp_path, monkeypatch):
    config = tmp_path / "mcp_config.json"
    config.write_text(json.dumps({"mcpServers": {"gmail": {
        "command": "python", "env": {"GMAIL_ADDRESS": "legacy@example.com", "OUTBOX": "out"},
    }}}))
    (tmp_path / ".env").write_text('GMAIL_ADDRESS=local@example.com\nGMAIL_APP_PASSWORD=local-secret\n')
    monkeypatch.setenv("GMAIL_ADDRESS", "exported@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "exported-secret")
    monkeypatch.setenv("UNRELATED_SECRET", "private")
    _, env = load_server_config(config)
    assert env == {"GMAIL_ADDRESS": "exported@example.com", "GMAIL_APP_PASSWORD": "exported-secret", "OUTBOX": "out"}


def test_explicit_empty_environment_does_not_fall_back_to_local_secret(tmp_path, monkeypatch):
    config = tmp_path / "mcp_config.json"
    config.write_text('{"mcpServers": {"gmail": {"command": "python"}}}')
    (tmp_path / ".env").write_text('GMAIL_APP_PASSWORD=local-secret\n')
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "")
    _, env = load_server_config(config)
    assert env["GMAIL_APP_PASSWORD"] == ""


# --- client, end to end over stdio -------------------------------------------

FAKE_SERVER = textwrap.dedent('''
    import json, os
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    mcp = MCPServer("fake-gmail", log_level="WARNING")

    @mcp.tool()
    def send_email(to: list[str], subject: str, body: str,
                   cc: list[str] | None = None, headers: dict[str, str] | None = None) -> str:
        if subject == "fail":
            raise ToolError("quota exceeded")
        with open(os.environ["OUTBOX"], "a") as fh:
            fh.write(json.dumps({"to": to, "subject": subject, "headers": headers}) + "\\n")
        return "ok"

    mcp.run("stdio")
''')


@pytest.fixture
def fake_server(tmp_path):
    script, outbox = tmp_path / "fake_server.py", tmp_path / "outbox.jsonl"
    script.write_text(FAKE_SERVER)
    config = tmp_path / "mcp_config.json"
    config.write_text(json.dumps({"mcpServers": {"gmail": {
        "command": "python", "args": [str(script)], "env": {"OUTBOX": str(outbox)},
    }}}))
    return load_server_config(config)[0], outbox


def test_sender_delivers_through_a_real_stdio_session(fake_server):
    params, outbox = fake_server
    with McpEmailSender(params, timeout=30) as sender:
        sender.send(to="me@gmail.com", subject="one", body="b", headers={"References": "<1@x>"})
        sender.send(to="me@gmail.com", subject="two", body="b", headers={"References": "<2@x>"})
    sent = [json.loads(line) for line in outbox.read_text().splitlines()]
    assert [(m["to"], m["subject"], m["headers"]["References"]) for m in sent] == [
        (["me@gmail.com"], "one", "<1@x>"), (["me@gmail.com"], "two", "<2@x>"),
    ]


def test_dotenv_credentials_reach_stdio_server(tmp_path, monkeypatch):
    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    script, outbox = tmp_path / "fake_server.py", tmp_path / "outbox.jsonl"
    script.write_text(
        'import os\n'
        'assert os.environ["GMAIL_ADDRESS"] == "dotenv@example.com"\n'
        'assert os.environ["GMAIL_APP_PASSWORD"] == "test-only"\n'
        'assert "UNRELATED_SECRET" not in os.environ\n' + FAKE_SERVER
    )
    config = tmp_path / "mcp_config.json"
    config.write_text(json.dumps({"mcpServers": {"gmail": {
        "command": "python", "args": [str(script)], "env": {"OUTBOX": str(outbox)},
    }}}))
    (tmp_path / ".env").write_text('GMAIL_ADDRESS=dotenv@example.com\nGMAIL_APP_PASSWORD=test-only\n')
    params, _ = load_server_config(config)
    with McpEmailSender(params, timeout=30) as sender:
        sender.send(to="test@example.com", subject="test", body="body", headers={})
    assert json.loads(outbox.read_text())["subject"] == "test"


def test_sender_raises_tool_errors_and_keeps_going(fake_server):
    params, outbox = fake_server
    with McpEmailSender(params, timeout=30) as sender:
        with pytest.raises(McpSendError, match="quota exceeded"):
            sender.send(to="me@gmail.com", subject="fail", body="b", headers={})
        sender.send(to="me@gmail.com", subject="after", body="b", headers={})
    assert "after" in outbox.read_text()


def test_sender_reports_a_server_that_cannot_start(tmp_path):
    config = tmp_path / "mcp_config.json"
    config.write_text(json.dumps({"mcpServers": {"gmail": {"command": "python", "args": [str(tmp_path / "missing.py")]}}}))
    with pytest.raises(McpSendError, match="could not start MCP server"):
        with McpEmailSender(load_server_config(config)[0], timeout=15):
            pass
