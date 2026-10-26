"""MCP tool implementations — one per Template API endpoint.

Plain async methods (no MCP SDK import here) so they can be unit-tested
with a mocked HTTP transport; ``server.py`` registers them on the MCP
server. Every tool returns a JSON string (``render_preview`` with
``show=True`` also a :class:`PngImage`); API failures become
:class:`ToolFailure` with the API's error code, message and — for an
invalid layout — the list of ``{path, code, message}`` errors, so the
model can fix the exact slot.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Literal

from .client import StudioAPIError, StudioClient

# HTML previews are returned inline; cap what goes back into the model's
# context (a long multi-page document is mostly repeated markup).
MAX_INLINE_HTML = 200_000

# import_svg reads at most this much from a local file (the API's limit).
MAX_SVG_BYTES = 1_000_000

# Starter styles (GET /starters lists which ones each starter comes in).
StarterStyle = Literal["classic", "folder", "wave", "diagonal", "sidebar", "bigtotal"]

# The Odoo models an email template can be written for.
EmailModel = Literal["sale.order", "account.move", "purchase.order", "res.partner"]


class ToolFailure(Exception):
    """Raised by a tool; ``server.py`` maps it to an MCP tool error."""


class PngImage:
    """A PNG a tool hands back to the model; ``server.py`` turns it into
    MCP image content."""

    def __init__(self, data: bytes):
        self.data = data


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def _fail(e: StudioAPIError) -> ToolFailure:
    out: dict[str, Any] = {"status": e.status, "code": e.code, "message": e.message}
    if isinstance(e.detail, dict):
        for key in ("errors", "warnings", "retry_after", "templates", "current_plan",
                    "required_plan", "upstream"):
            if key in e.detail:
                out[key] = e.detail[key]
    return ToolFailure(_dump(out))


def _default_output_dir() -> Path:
    """A folder only this user can reach: the user cache directory, not
    the shared temp directory, where another local user could plant a
    symlink under the name we write to."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "erp2pdf-studio-mcp"


def _output_dir() -> Path:
    chosen = os.environ.get("ERP2PDF_OUTPUT_DIR")
    d = Path(chosen) if chosen else _default_output_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not chosen and os.name != "nt":
        # The default folder must be ours and a real directory.
        st = os.lstat(d)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise RuntimeError(f"{d} is not a directory owned by this user; set ERP2PDF_OUTPUT_DIR")
    return d


def _write_file(path: Path, data: bytes) -> None:
    """Write through a fresh private temp file in the same folder, then
    rename it into place: a rename replaces whatever is at ``path`` (a
    symlink included) instead of writing through it."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".erp2pdf-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "preview"


class StudioTools:
    """The tool surface. Method names are the MCP tool names."""

    NAMES = (
        "list_models", "get_fields", "search_records", "get_sample_record",
        "list_templates", "get_template", "create_template", "update_template",
        "delete_template", "duplicate_template", "validate_layout", "get_values",
        "render_preview", "list_starters", "create_from_starter",
        "get_capabilities", "import_svg",
        "list_parts", "create_part", "update_part", "delete_part",
        "get_translations", "set_translations",
        "list_email_templates", "get_email_template", "create_email_template",
        "update_email_template", "delete_email_template", "preview_email",
    )

    def __init__(self, client: StudioClient):
        self.client = client

    async def _call(self, coro) -> str:
        try:
            return _dump(await coro)
        except StudioAPIError as e:
            raise _fail(e) from e

    # ── discovery ───────────────────────────────────────────────────

    async def list_models(self, search: str | None = None) -> str:
        """List the Odoo models a template can bind to (live from the
        customer's Odoo when a connector is configured, else bundled
        samples). Use the `model` value as layout.bind_model."""
        return await self._call(self.client.list_models(search))

    async def get_fields(self, model: str, search: str | None = None) -> str:
        """Fields of an Odoo model, plus `paths`: dotted paths one relation
        deep (partner_id.name, order_line.price_subtotal). A path with
        `in_table` belongs inside a table (repeater) bound to that value,
        where you write it as `row_path` (e.g. {{ price_subtotal }})."""
        return await self._call(self.client.get_fields(model, search))

    async def search_records(self, model: str, search: str | None = None,
                             limit: int = 20) -> str:
        """Search real records in the customer's Odoo (needs a configured
        connector) to pick a record_id for get_values / render_preview."""
        return await self._call(self.client.search_records(model, search, limit))

    async def get_sample_record(self, model: str, record_id: int | None = None) -> str:
        """The record previews are rendered with (real when a connector is
        configured, else the bundled sample): shows the data shape."""
        return await self._call(self.client.get_sample(model, record_id))

    # ── templates ───────────────────────────────────────────────────

    async def list_templates(self) -> str:
        """List the account's templates (id, name, bind_model, slot_count)."""
        return await self._call(self.client.list_templates())

    async def get_template(self, template_id: int) -> str:
        """Get one template with its full layout and mapping."""
        return await self._call(self.client.get_template(template_id))

    async def create_template(self, name: str, layout: dict, mapping: dict | None = None,
                              description: str | None = None) -> str:
        """Create a template from a Studio layout {bind_model, slots[],
        master_refs?, translations?}. The layout is validated first; on
        errors nothing is saved and the errors carry JSON paths."""
        return await self._call(self.client.create_template(name, layout, mapping, description))

    async def update_template(
        self,
        template_id: int,
        name: str | None = None,
        description: str | None = None,
        layout: dict | None = None,
        mapping: dict | None = None,
        layout_set: dict | None = None,
        upsert_slots: list[dict] | None = None,
        delete_slots: list[str] | None = None,
    ) -> str:
        """Change a template. Either send a whole new `layout`, or edit in
        place: `upsert_slots` adds slots or replaces those with the same id,
        `delete_slots` removes top-level slots by id, `layout_set` sets
        top-level keys such as master_refs or translations (null removes)."""
        return await self._call(self.client.update_template(
            template_id, name=name, description=description, layout=layout, mapping=mapping,
            layout_set=layout_set, upsert_slots=upsert_slots, delete_slots=delete_slots,
        ))

    async def delete_template(self, template_id: int) -> str:
        """Delete a template."""
        return await self._call(self.client.delete_template(template_id))

    async def duplicate_template(self, template_id: int, name: str | None = None) -> str:
        """Copy a template (default name: '<name> (copy)')."""
        return await self._call(self.client.duplicate_template(template_id, name))

    async def validate_layout(self, layout: dict, mapping: dict | None = None,
                              kind: Literal["template", "header", "footer"] = "template",
                              model: str | None = None, evaluate: bool = True,
                              record_id: int | None = None) -> str:
        """Validate a layout without saving: returns {ok, errors[], warnings[],
        stats}. Each issue has a JSON path (slots[3].row_template[1].content),
        a code and a message. With evaluate=true every expression is also run
        against a sample record; runtime failures come back as warnings."""
        return await self._call(self.client.validate_layout(
            layout, mapping, kind=kind, model=model, evaluate=evaluate, record_id=record_id))

    async def get_values(self, template_id: int | None = None, layout: dict | None = None,
                         mapping: dict | None = None, model: str | None = None,
                         record_id: int | None = None, language: str | None = None,
                         timezone: str | None = None) -> str:
        """What every {{ }} expression prints for one record, per slot
        (`slots`: path, template, value/error) and per table row (`tables`).
        Pass template_id for a saved template or layout for a draft. The
        cheapest way to check expressions without reading a PDF.
        `language` picks formatDate's "short"/"long"/"datetime" presets;
        `timezone` (IANA, Europe/Kyiv) is where datetimes print, unless the
        live record brings the document's own."""
        if template_id is not None:
            return await self._call(self.client.template_values(
                template_id, record_id=record_id, language=language, timezone=timezone))
        if layout is None:
            raise ToolFailure("pass template_id or layout")
        return await self._call(self.client.draft_values(
            layout, mapping, model=model, record_id=record_id, language=language,
            timezone=timezone))

    async def render_preview(self, template_id: int | None = None, layout: dict | None = None,
                             mapping: dict | None = None,
                             output: Literal["pdf", "html", "png"] = "pdf",
                             model: str | None = None, record_id: int | None = None,
                             language: str | None = None, timezone: str | None = None,
                             show: bool = True) -> str | list:
        """Render a preview (PREVIEW watermark, no credits) of a saved
        template (template_id) or an unsaved layout. output=html returns the
        HTML inline; pdf/png are saved to a local file and the path is
        returned (png = page 1). With output=png and show=true (default) the
        image itself comes back too: look at it to judge the design
        (alignment, overlaps, clipped text, contrast) and iterate."""
        try:
            if template_id is not None:
                res = await self.client.render_template(
                    template_id, output=output, record_id=record_id, language=language,
                    timezone=timezone)
                stem = f"template-{template_id}"
            elif layout is not None:
                res = await self.client.render_draft(
                    layout, mapping, output=output, model=model, record_id=record_id,
                    language=language, timezone=timezone)
                stem = "draft"
            else:
                raise ToolFailure("pass template_id or layout")
        except StudioAPIError as e:
            raise _fail(e) from e
        meta = {
            "content_type": res.content_type,
            "bytes": len(res.content),
            "pages": res.headers.get("x-output-pages"),
            "sample_source": res.headers.get("x-sample-source"),
            "sample_record_id": res.headers.get("x-sample-record-id"),
        }
        if output == "html":
            html = res.content.decode("utf-8", "replace")
            meta["truncated"] = len(html) > MAX_INLINE_HTML
            meta["html"] = html[:MAX_INLINE_HTML]
            return _dump(meta)
        ext = ".png" if output == "png" else ".pdf"
        path = _output_dir() / _safe_name(f"{stem}{ext}")
        _write_file(path, res.content)
        meta["path"] = str(path)
        if output == "png" and show:
            return [_dump(meta), PngImage(res.content)]
        return _dump(meta)

    async def list_starters(self) -> str:
        """Starting points: blank, quotation, invoice, purchase, product,
        each with the `styles` it comes in (classic, folder, wave, diagonal,
        sidebar, bigtotal) and whether a style uses the shared header/footer."""
        return await self._call(self.client.list_starters())

    async def create_from_starter(self, starter: Literal["blank", "quotation", "invoice",
                                                         "purchase", "product"],
                                  name: str, style: StarterStyle | None = None,
                                  model: str | None = None,
                                  date_format: str | None = None, header: bool = True,
                                  footer: bool = True) -> str:
        """Create a template from a starter layout (same as Studio's New
        template dialog). `style` picks the look (default classic): folder =
        light band with a folder tab, wave = dark wave header, diagonal =
        dark band with a slanted edge, sidebar = coloured column, bigtotal =
        the amount to pay set large. Every colour is a theme reference, so
        update_template(layout_set={"theme": {...}}) rebrands it. Classic
        reuses the shared 'Company header'/'Company footer' parts (created on
        first use); the bold styles draw their own letterhead. `model` is
        only for the blank starter."""
        return await self._call(self.client.create_from_starter(
            starter, name, model=model, date_format=date_format, header=header, footer=footer,
            style=style))

    # ── design ──────────────────────────────────────────────────────

    async def get_capabilities(self) -> str:
        """Read this before designing. Everything a layout can hold, from
        the same rules the validator applies: slot kinds and their keys with
        types and allowed values (text: letterSpacing, lineHeight,
        textTransform, valign, bgColor...; image: mask, grayscale; every
        slot: opacity, shadow, rotation, blendMode), the paint format
        (hex, theme.<key>, or a linear/radial gradient object), the `path`
        kind (free vector drawing: viewBox + paths[{d, fill, gradient,
        stroke, ...}]), page background, theme keys, bundled fonts, page
        sizes, limits, and ready `path_recipes` (folder tab, wave, diagonal
        band, chevron, hexagon, blob, corner arc) to copy into a path slot."""
        return await self._call(self.client.get_capabilities())

    async def import_svg(self, svg: str | None = None, path: str | None = None,
                         w: float | None = None, h: float | None = None) -> str:
        """Turn an SVG (a logo, an illustration, a shape drawn elsewhere)
        into a `path` slot body: returns {viewBox, paths, warnings, box,
        keepAspect}. Pass the markup as `svg`, or `path` to a local .svg file
        (max 1 MB). Create the slot as {"kind": "path", "x", "y", "w": box.w,
        "h": box.h, "viewBox", "keepAspect", "paths"}, or pass w/h for the
        size you will place it at (stroke widths are in pt for that size).
        Text, images, filters and masks are dropped and listed in warnings."""
        if (svg is None) == (path is None):
            raise ToolFailure("pass exactly one of svg or path")
        if path is not None:
            p = Path(path).expanduser()
            try:
                with p.open("rb") as f:
                    data = f.read(MAX_SVG_BYTES + 1)
            except OSError as e:
                raise ToolFailure(f"cannot read {p}: {e.strerror or e}") from e
        else:
            data = svg.encode("utf-8")
        if len(data) > MAX_SVG_BYTES:
            raise ToolFailure("the SVG is larger than 1 MB")
        return await self._call(self.client.convert_svg(data, w=w, h=h))

    # ── parts ───────────────────────────────────────────────────────

    async def list_parts(self, kind: Literal["header", "footer"] | None = None) -> str:
        """List the shared header/footer parts (templates use them through
        layout.master_refs = {header: id, footer: id})."""
        return await self._call(self.client.list_parts(kind))

    async def create_part(self, kind: Literal["header", "footer"], name: str, layout: dict,
                          description: str | None = None) -> str:
        """Create a header or footer part: {slots[]} in page coordinates (a
        header near the top, a footer near the bottom of the A4 page)."""
        return await self._call(self.client.create_part(kind, name, layout, description))

    async def update_part(self, part_id: int, name: str | None = None,
                          layout: dict | None = None, description: str | None = None) -> str:
        """Update a part's name, description and/or layout. Every template
        that uses the part changes with it."""
        return await self._call(self.client.update_part(
            part_id, name=name, layout=layout, description=description))

    async def delete_part(self, part_id: int, force: bool = False) -> str:
        """Delete a part. Refused (part_in_use) while templates use it,
        unless force=true."""
        return await self._call(self.client.delete_part(part_id, force))

    # ── translations ────────────────────────────────────────────────

    async def get_translations(self, template_id: int,
                               kind: Literal["pdf", "email"] = "pdf") -> str:
        """Every translatable string of a PDF template (kind=pdf, header and
        footer parts included) or an email template (kind=email: subject and
        body text), with its translation per target language, a `status` per
        language (translated, inherited, override, invalid, missing), glossary
        `suggestions` for missing cells and `missing` counts per language.
        A string that prints values keeps them as {{ placeholders }}; an email
        string keeps its inline tags (b, strong, i, em, u, a, span, code, br)
        as numbered placeholders <1>...</1> and <2/>, listed in `placeholders`
        (the element each stands for in `tags`)."""
        return await self._call(self.client.get_translations(template_id, kind=kind))

    async def set_translations(
        self,
        template_id: int,
        kind: Literal["pdf", "email"] = "pdf",
        translations: dict[str, dict[str, str | None]] | None = None,
        replace: bool = False,
        languages: list[str] | None = None,
        add_languages: list[str] | None = None,
        remove_languages: list[str] | None = None,
        source_language: str | None = None,
        fill_from_glossary: list[str] | None = None,
        remove_unused: bool = False,
    ) -> str:
        """Translate a template, many languages in one call.
        `translations` = {lang: {source string: text}} (lang: de, de_DE,
        fr_CH...; source strings exactly as get_translations lists them; null
        deletes a cell). A translation must keep exactly the source's
        {{ expressions }} and, in an email, its numbered tags <1>...</1>, <2/>
        (any order, each tag closed after it opens), else nothing is saved
        and the errors say which. replace=true replaces each language sent as a whole (PUT);
        default merges (PATCH). `fill_from_glossary` fills empty cells of the
        given languages from the built-in glossary. Returns the updated report
        plus `changed` and `warnings`."""
        return await self._call(self.client.set_translations(
            template_id, kind=kind, replace=replace, translations=translations,
            languages=languages, add_languages=add_languages,
            remove_languages=remove_languages, source_language=source_language,
            fill_from_glossary=fill_from_glossary, remove_unused=remove_unused,
        ))

    # ── email templates ─────────────────────────────────────────────

    async def list_email_templates(self) -> str:
        """List the account's HTML email templates (sent from Odoo by the
        connector's 'Send by email' wizard)."""
        return await self._call(self.client.list_email_templates())

    async def get_email_template(self, email_template_id: int) -> str:
        """Get one email template: subject, body_html, translations and the
        record `fields` it reads."""
        return await self._call(self.client.get_email_template(email_template_id))

    async def create_email_template(
        self,
        name: str,
        bind_model: EmailModel = "sale.order",
        subject: str = "",
        body_html: str = "",
        description: str | None = None,
        attach_template_id: int | None = None,
    ) -> str:
        """Create an email template. Subject and body use the PDF templates'
        expressions: {{ expr }} prints a value HTML-escaped, {{{ expr }}}
        inserts it as cleaned HTML. Use inline styles and tables (email
        clients). {{ odoo_url }} is the connected Odoo's https address:
        {{ odoo_url }}/logo.png?company={{ company_id.id }} is the company logo.
        attach_template_id = a PDF template to attach when sent."""
        return await self._call(self.client.create_email_template(
            name, bind_model=bind_model, subject=subject, body_html=body_html,
            description=description, attach_template_id=attach_template_id))

    async def update_email_template(
        self,
        email_template_id: int,
        name: str | None = None,
        subject: str | None = None,
        body_html: str | None = None,
        bind_model: EmailModel | None = None,
        description: str | None = None,
        attach_template_id: int | None = None,
        remove_attachment: bool = False,
    ) -> str:
        """Change an email template; only the fields given change.
        remove_attachment=true stops attaching a PDF."""
        changes: dict[str, Any] = {k: v for k, v in {
            "name": name, "subject": subject, "body_html": body_html,
            "bind_model": bind_model, "description": description,
            "attach_template_id": attach_template_id,
        }.items() if v is not None}
        if remove_attachment:
            changes["attach_template_id"] = None
        return await self._call(self.client.update_email_template(email_template_id, changes))

    async def delete_email_template(self, email_template_id: int) -> str:
        """Delete an email template."""
        return await self._call(self.client.delete_email_template(email_template_id))

    async def preview_email(
        self,
        email_template_id: int | None = None,
        subject: str | None = None,
        body_html: str | None = None,
        bind_model: EmailModel = "sale.order",
        translations: dict | None = None,
        record_id: int | None = None,
        language: str | None = None,
        timezone: str | None = None,
    ) -> str:
        """Render an email for one record: a saved one (email_template_id) or
        a draft (subject + body_html, optional translations). `language`
        (de_DE, fr...) shows it as a customer with that language gets it;
        `timezone` (IANA) is where its datetimes print.
        Returns {subject, html, errors, source, record}; no credits."""
        if email_template_id is not None:
            return await self._call(self.client.preview_email(
                email_template_id, record_id=record_id, language=language, timezone=timezone))
        if subject is None and body_html is None:
            raise ToolFailure("pass email_template_id, or subject and/or body_html")
        return await self._call(self.client.preview_draft_email(
            subject=subject or "", body_html=body_html or "", bind_model=bind_model,
            translations=translations, record_id=record_id, language=language,
            timezone=timezone))
