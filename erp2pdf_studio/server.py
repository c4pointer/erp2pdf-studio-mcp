"""MCP server (stdio) for the erp2pdf.com Template API.

Configuration (environment):

* ``ERP2PDF_API_KEY``   — an API token from the erp2pdf.com cabinet →
  API tokens (required). Issue a separate token for AI use so it can be
  revoked without touching the Odoo connector.
* ``ERP2PDF_BASE_URL``  — default ``https://erp2pdf.com``.
* ``ERP2PDF_OUTPUT_DIR`` — where rendered PDFs/PNGs are saved (default:
  ``~/.cache/erp2pdf-studio-mcp``, or ``%LOCALAPPDATA%\\erp2pdf-studio-mcp``
  on Windows; created with mode 0700).

Run: ``erp2pdf-studio-mcp`` or ``python -m erp2pdf_studio``.
"""
from __future__ import annotations

import functools
import sys

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from .client import StudioClient
from .tools import PngImage, StudioTools, ToolFailure

INSTRUCTIONS = """\
Build and test PDF templates on erp2pdf.com (Studio templates bound to Odoo
models). Recipe: list_models -> get_fields(model) -> create_from_starter (or
create_template) -> update_template (upsert_slots) -> validate_layout ->
get_values (check what each {{ }} prints) -> render_preview (pdf/png/html).
Coordinates are points on an A4 page (595 x 842). Text lives in `content`,
image/QR sources in `source`. Expressions: {{ name }}, {{ partner_id.name }},
{{ money(amount_total, currency_id) }}, {{ d ? formatDate(d, "short") : "" }}
("short"/"long"/"datetime" follow the document language; datetimes print in
the document's timezone).
No object literals. Tables are `repeater` slots bound to a one2many
($.order_line) whose row_template cells read line fields ({{ price_subtotal }}).
Translations (PDF and email templates): get_translations lists every static
string per language -> set_translations({lang: {source: text}}), keeping each
string's {{ expressions }}; fill_from_glossary covers common terms. Check with
render_preview / preview_email and language="de_DE". Email templates:
create_email_template / update_email_template / preview_email.
Design: get_capabilities first (kinds, keys, gradients, fonts, path_recipes).
Anything can be drawn: `path` slots hold SVG path data (viewBox + paths with
fill/gradient/stroke), shapes and text take gradients, opacity, shadow and
rotation, layout.background paints every page, layout.theme holds the brand
colours (refer to them as "theme.primary"). Start bold from
create_from_starter(style="folder"|"wave"|"diagonal"|"sidebar"|"bigtotal");
import_svg turns a logo or illustration into a path slot. Look at
render_preview(output="png") after each change and fix what you see.
Full spec: https://erp2pdf.com/api-docs#template-api and https://erp2pdf.com/llms.txt.
"""


def _wrap(fn):
    """Turn ToolFailure into the SDK's ToolError (an error result the
    model can read) while keeping the signature the SDK introspects."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            out = await fn(*args, **kwargs)
        except ToolFailure as e:
            raise ToolError(str(e)) from e
        if isinstance(out, list):
            return [Image(data=x.data, format="png") if isinstance(x, PngImage) else x
                    for x in out]
        return out
    return wrapper


def build_server(client: StudioClient) -> MCPServer:
    server = MCPServer(name="erp2pdf-studio", instructions=INSTRUCTIONS, version=__version__)
    tools = StudioTools(client)
    for name in StudioTools.NAMES:
        fn = getattr(tools, name)
        server.add_tool(_wrap(fn), name=name, description=(fn.__doc__ or "").strip(),
                        structured_output=False)
    return server


def main() -> None:
    try:
        client = StudioClient.from_env()
    except ValueError as e:
        print(f"erp2pdf-studio-mcp: {e}", file=sys.stderr)
        sys.exit(2)
    build_server(client).run("stdio")


if __name__ == "__main__":
    main()
