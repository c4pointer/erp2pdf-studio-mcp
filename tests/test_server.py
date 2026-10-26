"""Tests for the erp2pdf-studio MCP server with the HTTP layer mocked.

``httpx.MockTransport`` stands in for erp2pdf.com: each test asserts the
exact request a tool sends (method, path, body, Bearer header) and what
the tool hands back to the model.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from erp2pdf_studio.client import StudioClient
from erp2pdf_studio.tools import StudioTools, ToolFailure

KEY = "k" * 64


class Recorder:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], httpx.Response] = {}

    def add(self, method: str, path: str, response: httpx.Response) -> None:
        self.routes[(method, path)] = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        hit = self.routes.get((request.method, request.url.path))
        return hit if hit is not None else httpx.Response(404, json={"detail": {
            "code": "not_found", "message": "no route"}})

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def last_json(self) -> dict:
        return json.loads(self.last.content or b"{}")


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def tools(rec):
    client = StudioClient(KEY, "https://erp2pdf.test", transport=httpx.MockTransport(rec))
    return StudioTools(client)


def test_client_requires_key():
    with pytest.raises(ValueError):
        StudioClient("", "https://x")


def test_from_env(monkeypatch):
    monkeypatch.setenv("ERP2PDF_API_KEY", KEY)
    monkeypatch.setenv("ERP2PDF_BASE_URL", "https://staging.example/")
    c = StudioClient.from_env()
    assert str(c._http.base_url) == "https://staging.example"


async def test_bearer_header_and_list_templates(tools, rec):
    rec.add("GET", "/api/v1/studio/templates", httpx.Response(200, json={"items": [], "total": 0}))
    out = json.loads(await tools.list_templates())
    assert out == {"items": [], "total": 0}
    assert rec.last.headers["authorization"] == f"Bearer {KEY}"
    # The key never travels in the URL.
    assert KEY not in str(rec.last.url)


async def test_get_fields_and_models(tools, rec):
    rec.add("GET", "/api/v1/studio/models",
            httpx.Response(200, json={"items": [{"model": "sale.order"}]}))
    rec.add("GET", "/api/v1/studio/models/sale.order/fields",
            httpx.Response(200, json={"model": "sale.order", "items": [], "paths": []}))
    await tools.list_models("sale")
    assert rec.last.url.params["search"] == "sale"
    await tools.get_fields("sale.order")
    assert rec.last.url.params["depth"] == "1"
    assert "search" not in rec.last.url.params


async def test_search_records_and_sample(tools, rec):
    rec.add("GET", "/api/v1/studio/models/sale.order/records",
            httpx.Response(200, json={"items": [{"id": 7, "display_name": "S00007"}]}))
    rec.add("GET", "/api/v1/studio/models/sale.order/sample",
            httpx.Response(200, json={"source": "mock", "record": {"name": "SO0042"}}))
    out = json.loads(await tools.search_records("sale.order", "S000", limit=5))
    assert out["items"][0]["id"] == 7
    assert rec.last.url.params["limit"] == "5"
    out = json.loads(await tools.get_sample_record("sale.order", 7))
    assert rec.last.url.params["record_id"] == "7"


async def test_create_template_sends_layout(tools, rec):
    rec.add("POST", "/api/v1/studio/templates", httpx.Response(201, json={"id": 3, "warnings": []}))
    layout = {"bind_model": "sale.order", "slots": []}
    out = json.loads(await tools.create_template("Q", layout))
    assert out["id"] == 3
    assert rec.last_json() == {"name": "Q", "layout": layout, "mapping": {}, "description": None}


async def test_update_template_patch_ops(tools, rec):
    rec.add("PATCH", "/api/v1/studio/templates/3", httpx.Response(200, json={"id": 3}))
    await tools.update_template(3, upsert_slots=[{"id": "a"}], delete_slots=["b"],
                                layout_set={"master_refs": None})
    assert rec.last.method == "PATCH"
    assert rec.last_json() == {"upsert_slots": [{"id": "a"}], "delete_slots": ["b"],
                               "layout_set": {"master_refs": None}}


async def test_delete_and_duplicate(tools, rec):
    rec.add("DELETE", "/api/v1/studio/templates/3", httpx.Response(204))
    rec.add("POST", "/api/v1/studio/templates/3/duplicate", httpx.Response(201, json={"id": 4}))
    assert json.loads(await tools.delete_template(3)) == {"ok": True}
    assert json.loads(await tools.duplicate_template(3))["id"] == 4


async def test_validation_errors_reach_the_model(tools, rec):
    errors = [{"path": "slots[2].content", "code": "bad_expression", "message": "boom"}]
    rec.add("POST", "/api/v1/studio/templates", httpx.Response(422, json={"detail": {
        "code": "layout_invalid", "message": "1 error", "errors": errors, "warnings": []}}))
    with pytest.raises(ToolFailure) as ei:
        await tools.create_template("Q", {"slots": []})
    payload = json.loads(str(ei.value))
    assert payload["code"] == "layout_invalid"
    assert payload["errors"] == errors
    assert payload["status"] == 422


async def test_plan_error(tools, rec):
    rec.add("GET", "/api/v1/studio/parts", httpx.Response(402, json={"detail": {
        "code": "upgrade_required", "message": "Pro", "current_plan": "free",
        "required_plan": "pro"}}))
    with pytest.raises(ToolFailure) as ei:
        await tools.list_parts()
    assert json.loads(str(ei.value))["required_plan"] == "pro"


async def test_fastapi_422_list_detail(tools, rec):
    rec.add("POST", "/api/v1/studio/parts", httpx.Response(422, json={"detail": [
        {"loc": ["body", "kind"], "msg": "bad"}]}))
    with pytest.raises(ToolFailure) as ei:
        await tools.create_part("header", "H", {"slots": []})
    assert json.loads(str(ei.value))["code"] == "request_invalid"


async def test_validate_layout_body(tools, rec):
    rec.add("POST", "/api/v1/studio/templates/validate",
            httpx.Response(200, json={"ok": True, "errors": [], "warnings": []}))
    await tools.validate_layout({"slots": []}, kind="header", model="sale.order", evaluate=False)
    assert rec.last_json() == {"layout": {"slots": []}, "mapping": {}, "kind": "header",
                               "evaluate": False, "model": "sale.order"}


async def test_get_values_saved_and_draft(tools, rec):
    rec.add("POST", "/api/v1/studio/templates/3/values", httpx.Response(200, json={"slots": []}))
    rec.add("POST", "/api/v1/studio/values", httpx.Response(200, json={"slots": []}))
    await tools.get_values(template_id=3, record_id=9)
    assert rec.last_json() == {"record_id": 9}
    await tools.get_values(template_id=3, language="de_DE", timezone="Europe/Kyiv")
    assert rec.last_json() == {"language": "de_DE", "timezone": "Europe/Kyiv"}
    await tools.get_values(layout={"slots": []}, model="sale.order")
    assert rec.last.url.path == "/api/v1/studio/values"
    with pytest.raises(ToolFailure):
        await tools.get_values()


async def test_render_preview_saves_pdf(tools, rec, tmp_path, monkeypatch):
    monkeypatch.setenv("ERP2PDF_OUTPUT_DIR", str(tmp_path))
    rec.add("POST", "/api/v1/studio/templates/3/render", httpx.Response(
        200, content=b"%PDF-1.7 x", headers={"content-type": "application/pdf",
                                             "x-output-pages": "2", "x-sample-source": "live"}))
    out = json.loads(await tools.render_preview(template_id=3))
    assert out["pages"] == "2" and out["sample_source"] == "live"
    assert Path(out["path"]).read_bytes() == b"%PDF-1.7 x"
    assert out["path"].endswith("template-3.pdf")
    assert rec.last_json() == {"output": "pdf"}


async def test_render_preview_draft_png_and_html(tools, rec, tmp_path, monkeypatch):
    monkeypatch.setenv("ERP2PDF_OUTPUT_DIR", str(tmp_path))
    rec.add("POST", "/api/v1/studio/render", httpx.Response(
        200, content=b"\x89PNG", headers={"content-type": "image/png"}))
    meta, image = await tools.render_preview(layout={"slots": []}, output="png")
    out = json.loads(meta)
    assert out["path"].endswith("draft.png")
    assert image.data == b"\x89PNG"
    assert rec.last_json()["output"] == "png"
    out = json.loads(await tools.render_preview(layout={"slots": []}, output="png", show=False))
    assert out["path"].endswith("draft.png")
    rec.add("POST", "/api/v1/studio/render", httpx.Response(
        200, content=b"<html>ok</html>", headers={"content-type": "text/html"}))
    out = json.loads(await tools.render_preview(layout={"slots": []}, output="html"))
    assert out["html"] == "<html>ok</html>" and out["truncated"] is False
    with pytest.raises(ToolFailure):
        await tools.render_preview()


async def test_render_error(tools, rec):
    rec.add("POST", "/api/v1/studio/render", httpx.Response(429, json={"detail": {
        "code": "rate_limited", "message": "slow down", "retry_after": 12}}))
    with pytest.raises(ToolFailure) as ei:
        await tools.render_preview(layout={"slots": []})
    assert json.loads(str(ei.value))["retry_after"] == 12


async def test_starters_and_parts(tools, rec):
    rec.add("GET", "/api/v1/studio/starters", httpx.Response(200, json={"items": []}))
    rec.add("POST", "/api/v1/studio/templates/from-starter", httpx.Response(201, json={"id": 5}))
    rec.add("PUT", "/api/v1/studio/parts/2", httpx.Response(200, json={"id": 2}))
    rec.add("DELETE", "/api/v1/studio/parts/2", httpx.Response(204))
    await tools.list_starters()
    await tools.create_from_starter("quotation", "Q", date_format="01/02/2006")
    assert rec.last_json() == {"starter": "quotation", "name": "Q",
                               "date_format": "01/02/2006", "header": True, "footer": True}
    await tools.create_from_starter("invoice", "I", style="folder")
    assert rec.last_json()["style"] == "folder"
    await tools.update_part(2, name="New")
    assert rec.last_json() == {"name": "New"}
    await tools.delete_part(2, force=True)
    assert rec.last.url.params["force"] == "true"


async def test_starter_theme_and_preview(tools, rec, tmp_path, monkeypatch):
    monkeypatch.setenv("ERP2PDF_OUTPUT_DIR", str(tmp_path))
    rec.add("POST", "/api/v1/studio/templates/from-starter", httpx.Response(201, json={"id": 5}))
    await tools.create_from_starter("invoice", "I", style="wave", theme={"palette": "ocean"})
    assert rec.last_json()["theme"] == {"palette": "ocean"}
    rec.add("GET", "/api/v1/studio/starters/invoice/wave/preview.png", httpx.Response(
        200, content=b"\x89PNG", headers={"content-type": "image/png"}))
    meta, image = await tools.preview_starter("invoice", "wave", palette="ocean", width=240)
    assert image.data == b"\x89PNG" and json.loads(meta)["placeholder"] is False
    assert rec.last.url.params["palette"] == "ocean" and rec.last.url.params["w"] == "240"
    assert "font" not in rec.last.url.params


@pytest.mark.parametrize("bad", ["../x", "Invoice", "a/b", ""])
async def test_bad_starter_keys_never_reach_http(tools, rec, bad):
    with pytest.raises(ToolFailure):
        await tools.preview_starter(bad)
    assert rec.requests == []


async def test_capabilities(tools, rec):
    rec.add("GET", "/api/v1/studio/capabilities", httpx.Response(200, json={
        "kinds": {"path": {}}, "path_recipes": [{"name": "wave"}]}))
    out = json.loads(await tools.get_capabilities())
    assert out["path_recipes"][0]["name"] == "wave"


async def test_import_svg_sends_raw_markup(tools, rec, tmp_path):
    rec.add("POST", "/api/v1/studio/svg/convert", httpx.Response(200, json={
        "viewBox": [0, 0, 10, 10], "paths": [{"d": "M0 0 H10"}], "warnings": []}))
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 H10"/></svg>'
    out = json.loads(await tools.import_svg(svg=svg, w=120))
    assert out["paths"][0]["d"] == "M0 0 H10"
    assert rec.last.content == svg.encode()
    assert rec.last.headers["content-type"] == "image/svg+xml"
    assert rec.last.url.params["w"] == "120" and "h" not in rec.last.url.params
    f = tmp_path / "logo.svg"
    f.write_text(svg)
    await tools.import_svg(path=str(f))
    assert rec.last.content == svg.encode()


@pytest.mark.parametrize("kw", [{}, {"svg": "<svg/>", "path": "x.svg"}])
async def test_import_svg_needs_one_source(tools, rec, kw):
    with pytest.raises(ToolFailure):
        await tools.import_svg(**kw)
    assert rec.requests == []


async def test_import_svg_refuses_big_or_missing_files(tools, rec, tmp_path):
    big = tmp_path / "big.svg"
    big.write_bytes(b"<svg>" + b" " * 1_000_001)
    with pytest.raises(ToolFailure, match="1 MB"):
        await tools.import_svg(path=str(big))
    with pytest.raises(ToolFailure, match="cannot read"):
        await tools.import_svg(path=str(tmp_path / "missing.svg"))
    assert rec.requests == []


async def test_import_svg_reads_only_svg_files(tools, rec, tmp_path):
    secret = tmp_path / "id_ed25519"
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----")
    with pytest.raises(ToolFailure, match=r"\.svg file"):
        await tools.import_svg(path=str(secret))
    disguised = tmp_path / "key.svg"
    disguised.write_text("-----BEGIN OPENSSH PRIVATE KEY-----")
    with pytest.raises(ToolFailure, match="does not look like an SVG"):
        await tools.import_svg(path=str(disguised))
    link = tmp_path / "link.svg"
    link.symlink_to(secret)
    with pytest.raises(ToolFailure):
        await tools.import_svg(path=str(link))
    folder = tmp_path / "dir.svg"
    folder.mkdir()
    with pytest.raises(ToolFailure, match="regular file"):
        await tools.import_svg(path=str(folder))
    assert rec.requests == []


async def test_network_error(rec):
    def boom(request):
        raise httpx.ConnectError("down", request=request)
    t = StudioTools(StudioClient(KEY, "https://x", transport=httpx.MockTransport(boom)))
    with pytest.raises(ToolFailure) as ei:
        await t.list_templates()
    assert json.loads(str(ei.value))["code"] == "network_error"


async def test_token_scope_error_reaches_the_model(tools, rec):
    rec.add("GET", "/api/v1/studio/templates", httpx.Response(403, json={"detail": {
        "code": "token_scope", "error": "token_scope",
        "message": "This token is not allowed to use the Template API."}}))
    with pytest.raises(ToolFailure) as ei:
        await tools.list_templates()
    payload = json.loads(str(ei.value))
    assert payload["status"] == 403
    assert payload["code"] == "token_scope"


# ── Path segments: model names and ids are validated, never spliced ──

BAD_MODELS = [
    "../templates/5", "sale.order/../../templates", "sale.order?x=1", "sale.order#x",
    "Sale.Order", "sale..order", ".sale", "sale.", "1sale", "sale order", "sale%2forder",
    "sale.or\nder", "sale.order\nx", "", "   ", "a" * 129, None, 5, ["sale.order"],
]


@pytest.mark.parametrize("model", BAD_MODELS)
async def test_bad_model_names_never_reach_http(tools, rec, model):
    for call in (tools.client.get_fields(model), tools.client.search_records(model),
                 tools.client.get_sample(model)):
        with pytest.raises(ValueError) as ei:
            await call
        assert ei.value.code == "invalid_argument"
    assert rec.requests == []


async def test_bad_model_is_a_tool_error(tools, rec):
    with pytest.raises(ToolFailure) as ei:
        await tools.get_fields("../templates")
    payload = json.loads(str(ei.value))
    assert payload["code"] == "invalid_argument"
    assert payload["status"] == 0
    assert rec.requests == []


@pytest.mark.parametrize("model", ["sale.order", "account.move.line", "res_partner",
                                   "x_studio.model_2"])
async def test_good_model_names_build_one_segment(tools, rec, model):
    rec.add("GET", f"/api/v1/studio/models/{model}/fields",
            httpx.Response(200, json={"model": model, "items": [], "paths": []}))
    await tools.client.get_fields(model)
    assert rec.last.url.raw_path.decode().split("?")[0] == \
        f"/api/v1/studio/models/{model}/fields"


BAD_IDS = [0, -1, True, False, 1.5, 3.0, "3/../4", "../parts", "3?x=1", "", "abc", "-3",
           None, [3]]


@pytest.mark.parametrize("bad", BAD_IDS)
async def test_bad_ids_never_reach_http(tools, rec, bad):
    c = tools.client
    calls = (
        c.get_template(bad), c.replace_template(bad, "n", {}), c.update_template(bad, name="n"),
        c.delete_template(bad), c.duplicate_template(bad), c.template_values(bad),
        c.render_template(bad), c.get_part(bad), c.update_part(bad, name="n"),
        c.delete_part(bad),
    )
    for call in calls:
        with pytest.raises(ValueError) as ei:
            await call
        assert ei.value.code == "invalid_argument"
    assert rec.requests == []


async def test_id_as_decimal_string_is_accepted(tools, rec):
    rec.add("GET", "/api/v1/studio/templates/12", httpx.Response(200, json={"id": 12}))
    assert json.loads(await tools.get_template("12"))["id"] == 12
    assert rec.last.url.raw_path == b"/api/v1/studio/templates/12"


# ── Translations and email templates ─────────────────────────────────


async def test_get_translations_pdf_and_email(tools, rec):
    report = {"languages": ["de"], "strings": [], "missing": {"de": 0}}
    rec.add("GET", "/api/v1/studio/templates/3/translations", httpx.Response(200, json=report))
    rec.add("GET", "/api/v1/studio/email-templates/4/translations",
            httpx.Response(200, json=report))
    assert json.loads(await tools.get_translations(3)) == report
    assert json.loads(await tools.get_translations(4, kind="email")) == report
    assert rec.last.url.path == "/api/v1/studio/email-templates/4/translations"
    with pytest.raises(ToolFailure) as ei:
        await tools.get_translations(4, kind="docx")
    assert json.loads(str(ei.value))["code"] == "invalid_argument"


async def test_set_translations_merges_or_replaces(tools, rec):
    rec.add("PATCH", "/api/v1/studio/templates/3/translations",
            httpx.Response(200, json={"changed": 2}))
    rec.add("PUT", "/api/v1/studio/email-templates/4/translations",
            httpx.Response(200, json={"changed": 1}))
    cells = {"de": {"Total": "Summe", "Old": None}}
    await tools.set_translations(3, translations=cells, fill_from_glossary=["fr"])
    assert rec.last.method == "PATCH"
    assert rec.last_json() == {"translations": cells, "fill_from_glossary": ["fr"]}
    await tools.set_translations(4, kind="email", replace=True,
                                 translations={"fr": {"Hi": "Salut"}}, remove_unused=True)
    assert rec.last.method == "PUT"
    assert rec.last_json() == {"translations": {"fr": {"Hi": "Salut"}}, "remove_unused": True}


async def test_placeholder_errors_reach_the_model(tools, rec):
    errors = [{"path": "translations.de.Quotation {{ name }}", "code": "placeholder_mismatch",
               "message": "keep the expressions", "expected": ["{{ name }}"], "got": []}]
    rec.add("PATCH", "/api/v1/studio/templates/3/translations", httpx.Response(400, json={
        "detail": {"code": "translations_invalid", "message": "1 problem", "errors": errors}}))
    with pytest.raises(ToolFailure) as ei:
        await tools.set_translations(3, translations={"de": {"Quotation {{ name }}": "Angebot"}})
    payload = json.loads(str(ei.value))
    assert payload["code"] == "translations_invalid"
    assert payload["errors"] == errors


async def test_email_template_tools(tools, rec):
    rec.add("GET", "/api/v1/studio/email-templates", httpx.Response(200, json={"items": []}))
    rec.add("POST", "/api/v1/studio/email-templates", httpx.Response(201, json={"id": 4}))
    rec.add("GET", "/api/v1/studio/email-templates/4", httpx.Response(200, json={"id": 4}))
    rec.add("PATCH", "/api/v1/studio/email-templates/4", httpx.Response(200, json={"id": 4}))
    rec.add("DELETE", "/api/v1/studio/email-templates/4", httpx.Response(204))
    await tools.list_email_templates()
    out = json.loads(await tools.create_email_template(
        "Quote", subject="Quotation {{ name }}", body_html="<p>Hi</p>"))
    assert out["id"] == 4
    assert rec.last_json() == {"name": "Quote", "bind_model": "sale.order",
                               "subject": "Quotation {{ name }}", "body_html": "<p>Hi</p>"}
    await tools.get_email_template(4)
    await tools.update_email_template(4, subject="New", remove_attachment=True)
    assert rec.last_json() == {"subject": "New", "attach_template_id": None}
    assert json.loads(await tools.delete_email_template(4)) == {"ok": True}


async def test_preview_email_saved_and_draft(tools, rec):
    rec.add("POST", "/api/v1/studio/email-templates/4/preview",
            httpx.Response(200, json={"subject": "Angebot S1", "html": "<p/>"}))
    rec.add("POST", "/api/v1/studio/email-templates/preview",
            httpx.Response(200, json={"subject": "Total général", "html": ""}))
    out = json.loads(await tools.preview_email(4, language="de_DE"))
    assert out["subject"] == "Angebot S1"
    assert rec.last_json() == {"language": "de_DE"}
    await tools.preview_email(subject="Total",
                              translations={"strings": {"Total": {"fr": "Total général"}}},
                              language="fr")
    assert rec.last_json() == {"bind_model": "sale.order", "subject": "Total", "body_html": "",
                               "translations": {"strings": {"Total": {"fr": "Total général"}}},
                               "language": "fr"}
    with pytest.raises(ToolFailure):
        await tools.preview_email()


@pytest.mark.parametrize("bad", BAD_IDS)
async def test_bad_email_ids_never_reach_http(tools, rec, bad):
    c = tools.client
    for call in (c.get_email_template(bad), c.update_email_template(bad, {"name": "n"}),
                 c.delete_email_template(bad), c.preview_email(bad),
                 c.get_translations(bad), c.get_translations(bad, kind="email"),
                 c.set_translations(bad, translations={})):
        with pytest.raises(ValueError) as ei:
            await call
        assert ei.value.code == "invalid_argument"
    assert rec.requests == []


# ── The MCP layer itself (needs the SDK) ─────────────────────────────

mcp = pytest.importorskip("mcp")


async def test_server_lists_all_tools(rec):
    from erp2pdf_studio.server import build_server

    client = StudioClient(KEY, "https://x", transport=httpx.MockTransport(rec))
    server = build_server(client)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == set(StudioTools.NAMES)
    create = next(t for t in tools if t.name == "create_template")
    assert set(create.input_schema["required"]) == {"name", "layout"}
    assert "self" not in create.input_schema["properties"]
    render = next(t for t in tools if t.name == "render_preview")
    assert render.input_schema["properties"]["output"]["enum"] == ["pdf", "html", "png"]


async def test_server_call_tool_roundtrip(rec):
    from erp2pdf_studio.server import build_server

    rec.add("GET", "/api/v1/studio/templates/7", httpx.Response(200, json={"id": 7}))
    server = build_server(StudioClient(KEY, "https://x", transport=httpx.MockTransport(rec)))
    result = await server.call_tool("get_template", {"template_id": 7})
    text = result.content[0].text
    assert json.loads(text) == {"id": 7}


async def test_server_returns_the_png_preview_as_an_image(rec, tmp_path, monkeypatch):
    from erp2pdf_studio.server import build_server

    monkeypatch.setenv("ERP2PDF_OUTPUT_DIR", str(tmp_path))
    rec.add("POST", "/api/v1/studio/templates/7/render", httpx.Response(
        200, content=b"\x89PNG\r\n", headers={"content-type": "image/png"}))
    server = build_server(StudioClient(KEY, "https://x", transport=httpx.MockTransport(rec)))
    result = await server.call_tool("render_preview", {"template_id": 7, "output": "png"})
    kinds = [c.type for c in result.content]
    assert kinds == ["text", "image"]
    assert result.content[1].mime_type == "image/png"


async def test_server_tool_error_is_reported(rec):
    from mcp.server.mcpserver.exceptions import ToolError

    from erp2pdf_studio.server import build_server

    rec.add("GET", "/api/v1/studio/templates/7", httpx.Response(404, json={"detail": {
        "code": "template_not_found", "message": "template 7 not found"}}))
    server = build_server(StudioClient(KEY, "https://x", transport=httpx.MockTransport(rec)))
    with pytest.raises(ToolError) as ei:
        await server.call_tool("get_template", {"template_id": 7})
    assert "template_not_found" in str(ei.value)


async def test_default_output_dir_is_private_and_writes_replace_symlinks(tmp_path, monkeypatch):
    """No ERP2PDF_OUTPUT_DIR: previews go to the user cache folder (0700),
    and a symlink planted at the file name is replaced, not written through."""
    if os.name == "nt":
        pytest.skip("POSIX permissions")
    from erp2pdf_studio import tools as tools_mod

    monkeypatch.delenv("ERP2PDF_OUTPUT_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    d = tools_mod._output_dir()
    assert d == tmp_path / "cache" / "erp2pdf-studio-mcp"
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (d / "out.pdf").symlink_to(victim)
    tools_mod._write_file(d / "out.pdf", b"%PDF")
    assert victim.read_text() == "keep"
    assert not (d / "out.pdf").is_symlink()
    assert (d / "out.pdf").read_bytes() == b"%PDF"
