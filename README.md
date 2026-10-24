# erp2pdf-studio-mcp

[![CI](https://github.com/c4pointer/erp2pdf-studio-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/c4pointer/erp2pdf-studio-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)

An [MCP](https://modelcontextprotocol.io) server (stdio) that lets an AI agent
build and test PDF templates on [erp2pdf.com](https://erp2pdf.com) without the
Studio UI. The agent can discover the Odoo models and fields, create a
template from a starter or from scratch, validate it, see what every
expression prints for a real record, render a watermarked preview (PDF, PNG or
HTML), write the HTML email templates Odoo sends, and translate both kinds of
template into any language.

Each tool maps 1:1 onto an endpoint of the erp2pdf.com **Template API**
(`/api/v1/studio/*`). The layout format, the expression language, error codes
and rate limits are documented at
[erp2pdf.com/api-docs#template-api](https://erp2pdf.com/api-docs#template-api);
a compact, agent-friendly summary is at
[erp2pdf.com/llms.txt](https://erp2pdf.com/llms.txt).

## Contents

- [Requirements](#requirements)
- [Get an API token](#get-an-api-token)
- [Install](#install)
- [Configure your MCP client](#configure-your-mcp-client)
- [Environment variables](#environment-variables)
- [Tools](#tools)
- [Example session](#example-session)
- [Development](#development)
- [Versioning and support](#versioning-and-support)
- [License](#license)

## Requirements

- Python 3.10 or newer (or [`uv`](https://docs.astral.sh/uv/), which can
  fetch a suitable Python for you).
- An [erp2pdf.com](https://erp2pdf.com) account on the **Pro** plan or higher.
  Other plans get `402 upgrade_required` from the Template API.
- A **dedicated API token** issued with **Allow Template API** ticked (see
  below). Never reuse the Odoo connector's token.
- To work with live Odoo data (`search_records`, real sample records), an
  Odoo database connected to your erp2pdf.com account. Without it the API
  falls back to bundled sample records.

## Get an API token

1. Sign in at [erp2pdf.com](https://erp2pdf.com) and open the cabinet:
   **API tokens** (`https://erp2pdf.com/cabinet-tokens`) -> **New token**.
2. Name it after the agent (for example "Claude") and tick
   **Allow Template API (AI agents, MCP)**. Only tokens issued with that box
   ticked can call the Template API.
3. Copy the token: it is shown once. Put it in your MCP client configuration
   (below), not in a repository.

**Never reuse the Odoo connector's token.** It is stored in Odoo's settings,
has no Template API scope, and every tool call would fail with
`403 token_scope`. A dedicated token can also be revoked at any time without
breaking your Odoo connection.

The server sends the token only as an `Authorization: Bearer <token>` header,
and only to `ERP2PDF_BASE_URL`.

## Install

Run it without a permanent install, with
[`uvx`](https://docs.astral.sh/uv/guides/tools/):

```bash
uvx --from git+https://github.com/c4pointer/erp2pdf-studio-mcp erp2pdf-studio-mcp
```

Or install it with `pip` (in a virtual environment, or with `pipx`):

```bash
pip install git+https://github.com/c4pointer/erp2pdf-studio-mcp
erp2pdf-studio-mcp          # or: python -m erp2pdf_studio
```

To pin a release, append its tag to the URL, for example
`git+https://github.com/c4pointer/erp2pdf-studio-mcp@v0.2.0`.

Dependencies: [`mcp`](https://pypi.org/project/mcp/) 2.3.0 (the official MCP
Python SDK) and [`httpx`](https://www.python-httpx.org/).

The server speaks MCP over stdio and is started by your MCP client; you do not
run it by hand. Started without `ERP2PDF_API_KEY`, it prints
`erp2pdf-studio-mcp: ERP2PDF_API_KEY is not set` to stderr and exits with
status 2.

## Configure your MCP client

Replace `<token>` with the token from the cabinet.

### Claude Desktop

Settings -> Developer -> Edit config opens `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "erp2pdf-studio": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/c4pointer/erp2pdf-studio-mcp",
        "erp2pdf-studio-mcp"
      ],
      "env": {
        "ERP2PDF_API_KEY": "<token>",
        "ERP2PDF_OUTPUT_DIR": "/Users/you/Desktop/erp2pdf"
      }
    }
  }
}
```

Restart Claude Desktop after saving. If `uvx` is not on the PATH Claude
Desktop sees, use its absolute path (`which uvx`), or install the package with
`pip` and set `"command"` to the absolute path of `erp2pdf-studio-mcp`.

### Claude Code

```bash
claude mcp add erp2pdf-studio \
  -e ERP2PDF_API_KEY=<token> \
  -- uvx --from git+https://github.com/c4pointer/erp2pdf-studio-mcp erp2pdf-studio-mcp
```

Add `--scope user` to make it available in every project. For a project-level
`.mcp.json`, use the same `mcpServers` block as for Claude Desktop and keep
the token out of version control: write `"ERP2PDF_API_KEY": "${ERP2PDF_API_KEY}"`
and Claude Code takes the value from your environment.

### Cursor

`.cursor/mcp.json` in the project (or `~/.cursor/mcp.json` for all projects):

```json
{
  "mcpServers": {
    "erp2pdf-studio": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/c4pointer/erp2pdf-studio-mcp",
        "erp2pdf-studio-mcp"
      ],
      "env": {
        "ERP2PDF_API_KEY": "<token>"
      }
    }
  }
}
```

Any other MCP client that can start a stdio server works the same way:
command `uvx`, the arguments above, and the environment variables below.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ERP2PDF_API_KEY` | yes | - | API token from the erp2pdf.com cabinet, issued with "Allow Template API". |
| `ERP2PDF_BASE_URL` | no | `https://erp2pdf.com` | Base URL of the erp2pdf.com deployment to talk to. |
| `ERP2PDF_OUTPUT_DIR` | no | `~/.cache/erp2pdf-studio-mcp` (`%LOCALAPPDATA%\erp2pdf-studio-mcp` on Windows) | Where `render_preview` saves PDF and PNG previews. Created if missing, readable only by you. |

## Tools

Every tool returns JSON text. A failed call is an MCP tool error whose text is
JSON `{status, code, message, errors?, warnings?}`; for an invalid layout,
`errors` lists `{path, code, message}` so the agent can fix the exact slot.
Previews carry a PREVIEW watermark and cost no credits.

**Odoo data**

| Tool | What it does |
|---|---|
| `list_models` | List the Odoo models a template can bind to (live from your Odoo when connected, else bundled samples). |
| `get_fields` | Fields of a model, plus dotted paths one relation deep (`partner_id.name`, `order_line.price_subtotal`). |
| `search_records` | Search real records in your Odoo to pick a `record_id` for values and previews (needs a connected Odoo). |
| `get_sample_record` | The record previews are rendered with, to show the data shape. |

**PDF templates**

| Tool | What it does |
|---|---|
| `list_templates` | List the account's templates (id, name, bound model, slot count). |
| `get_template` | Get one template with its full layout and mapping. |
| `create_template` | Create a template from a Studio layout; it is validated first and nothing is saved on errors. |
| `update_template` | Replace the layout, or edit in place: upsert or delete slots, set top-level keys. |
| `delete_template` | Delete a template. |
| `duplicate_template` | Copy a template. |
| `validate_layout` | Validate a layout without saving, optionally evaluating every expression against a record. |
| `get_values` | What every `{{ }}` expression prints for one record, per slot and per table row. |
| `render_preview` | Render a watermarked preview as PDF or PNG (saved to `ERP2PDF_OUTPUT_DIR`) or HTML (returned inline). |
| `list_starters` | List the starter layouts: blank, quotation, invoice, purchase, product. |
| `create_from_starter` | Create a template from a starter, with the account's shared header and footer. |

**Header and footer parts**

| Tool | What it does |
|---|---|
| `list_parts` | List the shared header and footer parts. |
| `create_part` | Create a header or footer part. |
| `update_part` | Update a part; every template that uses it changes with it. |
| `delete_part` | Delete a part (refused while templates use it, unless `force=true`). |

**Translations** (PDF and email templates)

| Tool | What it does |
|---|---|
| `get_translations` | Every translatable string with its translation, status and glossary suggestions per language. |
| `set_translations` | Translate many languages in one call: merge or replace, add or remove languages, fill from the glossary. |

**Email templates**

| Tool | What it does |
|---|---|
| `list_email_templates` | List the HTML email templates sent from Odoo by the erp2pdf.com connector. |
| `get_email_template` | Get one email template: subject, body, translations and the fields it reads. |
| `create_email_template` | Create an email template, optionally attaching a PDF template. |
| `update_email_template` | Change an email template; only the fields given change. |
| `delete_email_template` | Delete an email template. |
| `preview_email` | Render a saved or draft email for one record, in a given language and timezone. |

`get_values`, `render_preview` and `preview_email` take `language` (for
example `de_DE`), which picks translations and the date presets, and
`timezone` (an IANA name such as `Europe/Berlin`), where datetimes print.

The full parameter list of each tool is in its MCP schema, which your client
shows to the agent.

## Example session

> Make me a quotation template for sale.order with our customer reference
> under the address, check it and show me page 1.

What the agent does:

1. `get_fields("sale.order")` finds `client_order_ref`.
2. `create_from_starter("quotation", "Quotation (AI)")` creates the template
   with a line table, totals and the shared header and footer.
3. `update_template(id, upsert_slots=[...])` adds a text slot under the
   partner address.
4. `validate_layout(...)` and `get_values(template_id=id)` confirm that every
   expression prints what it should.
5. `render_preview(template_id=id, output="png")` saves page 1 and returns
   its path.

> Translate it into German and French, and the quotation email too.

1. `get_translations(id)` lists the strings, with glossary suggestions.
2. `set_translations(id, fill_from_glossary=["de", "fr"])`, then
   `set_translations(id, translations={"de": {...}, "fr": {...}})` for the
   rest. A string with values (`Quotation {{ name }}`) keeps its `{{ }}`.
3. `render_preview(template_id=id, output="png", language="de_DE")`.
4. `get_translations(email_id, kind="email")`, then
   `set_translations(email_id, kind="email", ...)`, then
   `preview_email(email_id, language="fr_FR")`.

## Development

```bash
git clone https://github.com/c4pointer/erp2pdf-studio-mcp
cd erp2pdf-studio-mcp
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test,dev]"
ruff check .
pytest -q
```

The tests mock the HTTP layer with `httpx.MockTransport` and drive the server
through the SDK's `list_tools` / `call_tool`; they need no token and no
network. To try a local change in an MCP client, point the client's
`"command"` at `.venv/bin/erp2pdf-studio-mcp`.

Code layout:

- `erp2pdf_studio/client.py` - HTTP client, one method per Template API
  endpoint; validates model names and ids before they become URL path
  segments.
- `erp2pdf_studio/tools.py` - the MCP tools (plain async methods, testable
  without the SDK).
- `erp2pdf_studio/server.py` - registers the tools on the MCP server and runs
  it over stdio.

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Versioning and support

The project follows [Semantic Versioning](https://semver.org). While it is
0.x, a minor version may change tool parameters; every change is listed in
[CHANGELOG.md](CHANGELOG.md). Releases are tagged `vX.Y.Z`.

- Bugs and feature requests:
  [GitHub issues](https://github.com/c4pointer/erp2pdf-studio-mcp/issues).
- Account, plan or token questions: hello@erp2pdf.com.
- Security issues: see [SECURITY.md](SECURITY.md). Never post a token in an
  issue.

## License

[MIT](LICENSE). Copyright (c) 2026 Oleg Zubak (erp2pdf.com).
