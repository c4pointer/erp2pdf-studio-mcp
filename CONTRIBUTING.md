# Contributing

Thanks for helping improve erp2pdf-studio-mcp. Bug reports, fixes,
documentation improvements and new tool ideas are all welcome.

## Before you start

- For anything larger than a small fix, open an issue first so we can agree
  on the approach.
- This server is a thin wrapper around the erp2pdf.com Template API. A tool
  can only do what the API offers; if you need a new API capability, describe
  it in a feature request and we will discuss it with the API.
- Security problems go to hello@erp2pdf.com, not to a public issue. See
  [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/c4pointer/erp2pdf-studio-mcp
cd erp2pdf-studio-mcp
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test,dev]"
```

Python 3.10 is the oldest supported version; CI runs 3.10 to 3.13.

## Checks

Run both before you push; CI runs the same commands:

```bash
ruff check .
pytest -q
```

The tests mock the HTTP layer with `httpx.MockTransport`, so they need no
token and no network. Never put a real token, a customer's data or a real
Odoo hostname into a test, a fixture or an example.

## Guidelines

- Keep the layering: `client.py` talks HTTP (one method per endpoint),
  `tools.py` holds the tools as plain async methods with no MCP SDK import,
  `server.py` registers them. A new tool is a client method, a tool method
  whose name is added to `StudioTools.NAMES`, and tests for both.
- A tool's docstring is the description the agent reads. Write it for the
  model: what the tool does, the important parameters, what it returns.
- Validate anything that becomes part of a URL path in the client, before a
  request is sent (see `_model_segment` and `_id_segment`).
- Add a test for every behaviour change: assert the exact request (method,
  path, body) and what the tool returns to the model.
- Update `README.md` when tools or configuration change, and add a line under
  `[Unreleased]` in `CHANGELOG.md`.
- Everything in the repository is written in English: code, comments,
  docstrings, docs and commit messages.

## Pull requests

- One topic per pull request, with a clear description of what changes and
  why.
- Commit messages: a short summary line in the imperative mood, then a body
  that explains the reason for the change when it is not obvious.
- Make sure `ruff check .` and `pytest -q` pass.

By contributing you agree that your contribution is licensed under the
[MIT License](LICENSE) and that you follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
