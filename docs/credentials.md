# Gmail credentials

The MCP JSON config describes how to start the server. Keep credentials in
environment variables, with an optional `.env` file for local development.
Both the pipeline and the standalone Gmail MCP server support this setup.

## Local setup

```sh
python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
cp mcp_config.json.example mcp_config.json
```

Edit `.env` with your Gmail address and Gmail App Password:

```dotenv
GMAIL_ADDRESS=your.name@gmail.com
GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
```

Use a Gmail App Password, not your normal Google account password. The `.env`
file is plaintext, is ignored by Git, and should remain readable only by your
user. Commit `.env.example` and `mcp_config.json.example` with placeholders only.
Do not paste credentials into logs, screenshots, prompts, or documentation.

The pipeline reads `.env` beside the selected MCP config. When an AI tool starts
`mcp_server/gmail_server.py` directly, the server reads the project's root `.env`.
No shell `source` command is needed. Values are literal; `${...}` expansion is
disabled so passwords are preserved.

Exported environment variables take precedence over `.env`. The pipeline also
reads the MCP config's `env` entries as a last fallback.
`mcp_config.json.example` uses that block for placeholder credentials only,
as a template showing the structure. Keep your real credentials in `.env` or
the environment, not in the JSON. Explicitly empty exported
variables stay empty and do not fall back to a stored credential.

## Run

```sh
python main.py                 # dry run; sends nothing
python main.py notify --send-live --limit 1
```

Live notices go to `GMAIL_ADDRESS` by default. `--test-recipient` can select
another test inbox. The `--send-live` flag is still required to send anything.

## Deployment

In CI or a deployed service, supply `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`
through the platform's secret store as environment variables. A `.env` file is
optional and should not be packaged into an image or deployment artifact. The
pipeline explicitly forwards only these two credential variables, alongside
any settings intentionally supplied in the MCP config's `env` block, to the
MCP server. MCP also supplies its normal subprocess environment defaults.
