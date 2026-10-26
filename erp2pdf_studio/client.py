"""HTTP client for the erp2pdf.com Template API (/api/v1/studio/*).

Thin and dependency-light on purpose: one method per endpoint, JSON in,
JSON (or bytes for renders) out, every non-2xx turned into
:class:`StudioAPIError` carrying the API's stable error ``code``. The MCP
server wraps these calls; tests drive them through an
``httpx.MockTransport``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_BASE_URL = "https://erp2pdf.com"
API_PREFIX = "/api/v1/studio"
USER_AGENT = "erp2pdf-studio-mcp/0.2"

# Odoo technical model name (sale.order, account.move.line). Same shape the
# API enforces; checked here too because the name becomes a URL path
# segment, and a value such as "../templates/5" or "x?y" must never reach
# the HTTP layer as a different route.
MODEL_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
MAX_MODEL_LEN = 128


class StudioAPIError(Exception):
    """A non-2xx answer. ``code`` is the API's stable error code
    (``layout_invalid``, ``upgrade_required``, ...); ``detail`` is the
    full error object (with ``errors``/``warnings`` for validation)."""

    def __init__(self, status: int, code: str, message: str, detail: Any = None):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


class InvalidArgument(StudioAPIError, ValueError):
    """A model name or id the client refuses to put into a URL. Raised
    before any request is sent; a :class:`StudioAPIError` so the tool layer
    reports it like an API error (code ``invalid_argument``)."""

    def __init__(self, message: str):
        super().__init__(0, "invalid_argument", message)


def _model_segment(model: Any) -> str:
    """Validate an Odoo model name and return it as one encoded path segment."""
    if not isinstance(model, str):
        raise InvalidArgument("model must be a string such as sale.order")
    name = model.strip()
    if not name or len(name) > MAX_MODEL_LEN or not MODEL_RE.fullmatch(name):
        raise InvalidArgument(
            f"model must be an Odoo technical name such as sale.order, got {model!r}")
    return quote(name, safe="")


def _id_segment(value: Any, what: str = "id") -> str:
    """Validate a record id (positive int; a decimal string is accepted,
    floats and booleans are not) and return it as an encoded path segment."""
    if isinstance(value, bool):
        raise InvalidArgument(f"{what} must be a positive integer, got {value!r}")
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        value = int(value.strip())
    if not isinstance(value, int) or value <= 0:
        raise InvalidArgument(f"{what} must be a positive integer, got {value!r}")
    return quote(str(value), safe="")


def _template_path(template_id: Any, suffix: str = "") -> str:
    return f"/templates/{_id_segment(template_id, 'template_id')}{suffix}"


def _part_path(part_id: Any) -> str:
    return f"/parts/{_id_segment(part_id, 'part_id')}"


def _model_path(model: Any, suffix: str) -> str:
    return f"/models/{_model_segment(model)}{suffix}"


def _email_path(email_id: Any, suffix: str = "") -> str:
    return f"/email-templates/{_id_segment(email_id, 'email_template_id')}{suffix}"


def _translations_path(template_id: Any, kind: str) -> str:
    if kind == "email":
        return _email_path(template_id, "/translations")
    if kind == "pdf":
        return _template_path(template_id, "/translations")
    raise InvalidArgument(f"kind must be 'pdf' or 'email', got {kind!r}")


@dataclass
class BinaryResult:
    content: bytes
    content_type: str
    headers: dict[str, str]


class StudioClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("ERP2PDF_API_KEY is not set")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT,
                     "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(cls, **kw) -> StudioClient:
        return cls(
            api_key=os.environ.get("ERP2PDF_API_KEY", "").strip(),
            base_url=os.environ.get("ERP2PDF_BASE_URL", DEFAULT_BASE_URL).strip()
            or DEFAULT_BASE_URL,
            **kw,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── plumbing ────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, *, json: Any = None,
                       params: dict | None = None, content: bytes | None = None,
                       headers: dict | None = None) -> httpx.Response:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            r = await self._http.request(method, API_PREFIX + path, json=json, params=params,
                                         content=content, headers=headers)
        except httpx.HTTPError as e:
            raise StudioAPIError(0, "network_error", str(e)) from e
        if r.status_code >= 400:
            raise _error_from(r)
        return r

    async def _json(self, method: str, path: str, **kw) -> Any:
        r = await self._request(method, path, **kw)
        if r.status_code == 204 or not r.content:
            return {"ok": True}
        return r.json()

    # ── templates ───────────────────────────────────────────────────

    async def list_templates(self) -> dict:
        return await self._json("GET", "/templates")

    async def get_template(self, template_id: int) -> dict:
        return await self._json("GET", _template_path(template_id))

    async def create_template(self, name: str, layout: dict, mapping: dict | None = None,
                              description: str | None = None) -> dict:
        return await self._json("POST", "/templates", json={
            "name": name, "layout": layout, "mapping": mapping or {},
            "description": description,
        })

    async def replace_template(self, template_id: int, name: str, layout: dict,
                               mapping: dict | None = None,
                               description: str | None = None) -> dict:
        return await self._json("PUT", _template_path(template_id), json={
            "name": name, "layout": layout, "mapping": mapping or {},
            "description": description,
        })

    async def update_template(self, template_id: int, **changes: Any) -> dict:
        body = {k: v for k, v in changes.items() if v is not None}
        return await self._json("PATCH", _template_path(template_id), json=body)

    async def delete_template(self, template_id: int) -> dict:
        return await self._json("DELETE", _template_path(template_id))

    async def duplicate_template(self, template_id: int, name: str | None = None) -> dict:
        return await self._json("POST", _template_path(template_id, "/duplicate"),
                                json={"name": name} if name else {})

    async def validate_layout(self, layout: dict, mapping: dict | None = None, *,
                              kind: str = "template", model: str | None = None,
                              evaluate: bool = True, record_id: int | None = None) -> dict:
        body: dict[str, Any] = {"layout": layout, "mapping": mapping or {}, "kind": kind,
                                "evaluate": evaluate}
        if model:
            body["model"] = model
        if record_id:
            body["record_id"] = record_id
        return await self._json("POST", "/templates/validate", json=body)

    async def template_values(self, template_id: int, *, record_id: int | None = None,
                              language: str | None = None,
                              timezone: str | None = None) -> dict:
        return await self._json("POST", _template_path(template_id, "/values"), json=_drop_none(
            {"record_id": record_id, "language": language, "timezone": timezone}))

    async def draft_values(self, layout: dict, mapping: dict | None = None, *,
                           model: str | None = None, record_id: int | None = None,
                           language: str | None = None, timezone: str | None = None) -> dict:
        return await self._json("POST", "/values", json=_drop_none({
            "layout": layout, "mapping": mapping or {}, "model": model,
            "record_id": record_id, "language": language, "timezone": timezone,
        }))

    async def render_template(self, template_id: int, *, output: str = "pdf",
                              record_id: int | None = None,
                              language: str | None = None,
                              timezone: str | None = None) -> BinaryResult:
        r = await self._request("POST", _template_path(template_id, "/render"), json=_drop_none(
            {"output": output, "record_id": record_id, "language": language,
             "timezone": timezone}))
        return _binary(r)

    async def render_draft(self, layout: dict, mapping: dict | None = None, *,
                           output: str = "pdf", model: str | None = None,
                           record_id: int | None = None, language: str | None = None,
                           name: str = "draft", timezone: str | None = None) -> BinaryResult:
        r = await self._request("POST", "/render", json=_drop_none({
            "layout": layout, "mapping": mapping or {}, "output": output, "model": model,
            "record_id": record_id, "language": language, "name": name, "timezone": timezone,
        }))
        return _binary(r)

    async def list_starters(self) -> dict:
        return await self._json("GET", "/starters")

    async def create_from_starter(self, starter: str, name: str, *, model: str | None = None,
                                  date_format: str | None = None, header: bool = True,
                                  footer: bool = True, style: str | None = None) -> dict:
        return await self._json("POST", "/templates/from-starter", json=_drop_none({
            "starter": starter, "style": style, "name": name, "model": model,
            "date_format": date_format, "header": header, "footer": footer,
        }))

    # ── design ──────────────────────────────────────────────────────

    async def get_capabilities(self) -> dict:
        return await self._json("GET", "/capabilities")

    async def convert_svg(self, svg: bytes, *, w: float | None = None,
                          h: float | None = None) -> dict:
        r = await self._request("POST", "/svg/convert", params={"w": w, "h": h}, content=svg,
                                headers={"Content-Type": "image/svg+xml"})
        return r.json()

    # ── parts ───────────────────────────────────────────────────────

    async def list_parts(self, kind: str | None = None) -> dict:
        return await self._json("GET", "/parts", params={"kind": kind})

    async def get_part(self, part_id: int) -> dict:
        return await self._json("GET", _part_path(part_id))

    async def create_part(self, kind: str, name: str, layout: dict,
                          description: str | None = None) -> dict:
        return await self._json("POST", "/parts", json=_drop_none({
            "kind": kind, "name": name, "layout": layout, "description": description}))

    async def update_part(self, part_id: int, *, name: str | None = None,
                          layout: dict | None = None, description: str | None = None) -> dict:
        return await self._json("PUT", _part_path(part_id), json=_drop_none({
            "name": name, "layout": layout, "description": description}))

    async def delete_part(self, part_id: int, force: bool = False) -> dict:
        return await self._json("DELETE", _part_path(part_id),
                                params={"force": "true" if force else None})

    # ── translations ────────────────────────────────────────────────

    async def get_translations(self, template_id: int, *, kind: str = "pdf") -> dict:
        return await self._json("GET", _translations_path(template_id, kind))

    async def set_translations(self, template_id: int, *, kind: str = "pdf",
                               replace: bool = False, **changes: Any) -> dict:
        body = {k: v for k, v in changes.items() if v is not None and v is not False}
        return await self._json("PUT" if replace else "PATCH",
                                _translations_path(template_id, kind), json=body)

    # ── email templates ─────────────────────────────────────────────

    async def list_email_templates(self) -> dict:
        return await self._json("GET", "/email-templates")

    async def get_email_template(self, email_id: int) -> dict:
        return await self._json("GET", _email_path(email_id))

    async def create_email_template(self, name: str, *, bind_model: str = "sale.order",
                                    subject: str = "", body_html: str = "",
                                    description: str | None = None,
                                    attach_template_id: int | None = None) -> dict:
        return await self._json("POST", "/email-templates", json=_drop_none({
            "name": name, "bind_model": bind_model, "subject": subject, "body_html": body_html,
            "description": description, "attach_template_id": attach_template_id,
        }))

    async def update_email_template(self, email_id: int, changes: dict) -> dict:
        """PATCH with ``changes`` as given: a key set to None is sent as
        null (``attach_template_id: null`` removes the attachment)."""
        return await self._json("PATCH", _email_path(email_id), json=changes)

    async def delete_email_template(self, email_id: int) -> dict:
        return await self._json("DELETE", _email_path(email_id))

    async def preview_email(self, email_id: int, *, record_id: int | None = None,
                            language: str | None = None, timezone: str | None = None) -> dict:
        return await self._json("POST", _email_path(email_id, "/preview"), json=_drop_none(
            {"record_id": record_id, "language": language, "timezone": timezone}))

    async def preview_draft_email(self, *, subject: str, body_html: str,
                                  bind_model: str = "sale.order",
                                  translations: dict | None = None,
                                  record_id: int | None = None,
                                  language: str | None = None,
                                  timezone: str | None = None) -> dict:
        return await self._json("POST", "/email-templates/preview", json=_drop_none({
            "bind_model": bind_model, "subject": subject, "body_html": body_html,
            "translations": translations, "record_id": record_id, "language": language,
            "timezone": timezone,
        }))

    # ── models ──────────────────────────────────────────────────────

    async def list_models(self, search: str | None = None) -> dict:
        return await self._json("GET", "/models", params={"search": search})

    async def get_fields(self, model: str, search: str | None = None, depth: int = 1) -> dict:
        return await self._json("GET", _model_path(model, "/fields"),
                                params={"search": search, "depth": depth})

    async def search_records(self, model: str, search: str | None = None,
                             limit: int = 20) -> dict:
        return await self._json("GET", _model_path(model, "/records"),
                                params={"search": search, "limit": limit})

    async def get_sample(self, model: str, record_id: int | None = None) -> dict:
        return await self._json("GET", _model_path(model, "/sample"),
                                params={"record_id": record_id})


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _binary(r: httpx.Response) -> BinaryResult:
    return BinaryResult(
        content=r.content,
        content_type=r.headers.get("content-type", "application/octet-stream"),
        headers={k.lower(): v for k, v in r.headers.items()},
    )


def _error_from(r: httpx.Response) -> StudioAPIError:
    try:
        body = r.json()
    except ValueError:
        return StudioAPIError(r.status_code, "http_error", r.text[:500])
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return StudioAPIError(r.status_code, str(detail.get("code") or "error"),
                              str(detail.get("message") or ""), detail)
    if isinstance(detail, list):  # FastAPI request validation (422)
        return StudioAPIError(r.status_code, "request_invalid", "the request body is invalid",
                              {"errors": detail})
    return StudioAPIError(r.status_code, "error", str(detail))
