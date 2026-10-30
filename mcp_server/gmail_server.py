"""Gmail MCP server: exposes a `send_email` tool over stdio.

Sends through Gmail's SMTP server with an App Password, so no Google Cloud
project or OAuth flow is needed. Configure with environment variables (set
in the "env" block of mcp_config.json):

    GMAIL_ADDRESS       the Gmail account to send from
    GMAIL_APP_PASSWORD  16-character App Password (spaces are ignored)

Run directly (`python mcp_server/gmail_server.py`) or let an MCP client
launch it; it speaks MCP over stdin/stdout.
"""

import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # implicit TLS

# Headers a caller may set: threading headers plus any X- header. Everything
# else (From, To, Subject, Date, MIME) is controlled by the server.
_ALLOWED_HEADERS = {"references", "in-reply-to", "reply-to"}
_ADDRESS = re.compile(r"^[^@\s<>,]+@[^@\s<>,]+\.[^@\s<>,]+$")

mcp = MCPServer(
    "gmail",
    instructions="Send plain-text email from the configured Gmail account with send_email.",
    log_level="WARNING",
)


@mcp.tool()
def send_email(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """Send a plain-text email from the configured Gmail account.

    Args:
        to: Recipient email addresses.
        subject: Subject line.
        body: Plain-text body.
        cc: Optional CC addresses.
        headers: Optional extra headers: References, In-Reply-To, Reply-To or any X-* header.

    Returns a confirmation including the sent message's Message-ID.
    """
    # Expected failures are raised as ToolError so the caller sees the reason;
    # the SDK hides the text of any other exception.
    sender = _require_env("GMAIL_ADDRESS")
    password = _require_env("GMAIL_APP_PASSWORD").replace(" ", "")
    try:
        msg = build_message(sender, to, subject, body, cc or [], headers or {})
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(sender, password)
            refused = smtp.send_message(msg)  # recipients the server rejected
    except smtplib.SMTPAuthenticationError as exc:
        raise ToolError(
            "Gmail rejected the login. GMAIL_APP_PASSWORD must be an App Password "
            "(Google Account > Security > App passwords), not the account password."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise ToolError(f"SMTP error: {type(exc).__name__}: {exc}") from exc
    if refused:
        delivered = [a for a in [*to, *(cc or [])] if a not in refused]
        raise ToolError(
            f"partially delivered: rejected {', '.join(refused)}; "
            f"accepted {', '.join(delivered) or 'none'} (Message-ID: {msg['Message-ID']})"
        )
    return f"Sent to {', '.join(to)} (Message-ID: {msg['Message-ID']})"


def build_message(
    sender: str, to: list[str], subject: str, body: str, cc: list[str], headers: dict[str, str]
) -> EmailMessage:
    if not to:
        raise ValueError("at least one recipient is required")
    for address in [*to, *cc]:
        if not _ADDRESS.match(address):
            raise ValueError(f"invalid email address: {address!r}")
    for name, value in headers.items():
        if name.lower() not in _ALLOWED_HEADERS and not name.lower().startswith("x-"):
            raise ValueError(f"header not allowed: {name}")
        if "\r" in value or "\n" in value:
            raise ValueError(f"header {name} contains a line break")
    if "\r" in subject or "\n" in subject:
        raise ValueError("subject contains a line break")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[1])
    for name, value in headers.items():
        msg[name] = value
    msg.set_content(body)
    return msg


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ToolError(f"{name} is not set; add it to the server's env in mcp_config.json")
    return value


if __name__ == "__main__":
    mcp.run("stdio")
