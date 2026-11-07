# Supported Codex connection — checked 2026-11-07

Official OpenAI documentation describes shared MCP configuration for local desktop,
CLI and IDE clients, with STDIO and Streamable HTTP transports and tool allowlists:
[OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
It documents config.toml, command/args/cwd/env_vars for STDIO and bearer-token env
configuration for HTTP. This is a client connection mechanism, not an assertion
that Codex Desktop exposes a local application-control REST API.

G provides a STDIO bridge built with the maintained SDK 1.x FastMCP interface:
[official Python SDK 1.x reference](https://py.sdk.modelcontextprotocol.io/v1/).
SDK version 1.30.0 is pinned for repeatability; moving to SDK 2.x requires an
explicit adapter migration and repeat of the subprocess interoperability test.
The bridge talks to E's loopback REST API; it does not require the OpenAI API or
an OpenAI API key. E's gateway token is an independent scoped application credential.

## Installation after E integration, not performed by G

Create a dedicated venv and install `src/control/requirements-mcp.txt`. Example
configuration (replace absolute interpreter/repository paths):

```toml
[mcp_servers.bybit_historical_lab]
command = "C:/path/to/control-venv/Scripts/python.exe"
args = ["-m", "src.control.bridge"]
cwd = "C:/path/to/tester"
env_vars = ["LAB_CONTROL_URL", "LAB_CONTROL_TOKEN"]
enabled_tools = ["control_capabilities", "control_plan", "control_submit", "control_job", "control_cancel", "control_events", "control_artifacts", "control_audit"]
startup_timeout_sec = 10
tool_timeout_sec = 15
```

Provision `LAB_CONTROL_URL=http://127.0.0.1:8765` and a short-lived random
`LAB_CONTROL_TOKEN` through E's trusted credential mechanism. Do not paste tokens
into Git, prompts, process arguments or configuration examples. The bridge permits
only literal 127.0.0.1/::1 origins with explicit ports, disables proxies and redirects,
uses bounded responses and a 10-second request timeout. No arbitrary URL tool.

The host launches `python -m src.control.bridge`; stdout is reserved for MCP. There
is no tool for human approval, credential issuance, worker lease operations,
filesystem access, arbitrary shell/SQL/code or real exchange orders. Host tool
approval settings are an additional layer; the gateway's human approval is separate.

The bridge process has its gateway credential in memory/environment. Run it under
a least-privilege account; it is not a sandbox against code already executing as
that OS user. Keep exchange/database secrets out of this process entirely.
Provider output remains untrusted data even after structural validation. No pattern
filter can prove arbitrary text contains no secret or malicious instruction; E
must emit allowlisted projections and never include raw secret-bearing errors/logs.

## Verification

```powershell
python -m pip install -r src/control/requirements-mcp.txt
python -m unittest discover -s tests/control -v
python -m unittest discover -v
python -m pip check
```

Run the second test command in the normal application environment containing its
existing requirements. The first suite includes a real SDK ClientSession launching
the bridge over STDIO, listing its eight tools and calling the authenticated
capabilities route on an ephemeral loopback HTTP fixture. It rejects an unknown
approval tool. This does not attest to actual Desktop installation/account support
or E's production HTTP listener, which are separate integration gates.
