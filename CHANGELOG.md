# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-10-26

### Added

- `preview_starter(starter, style, palette?, font?, width?)`: page 1 of a starter style on sample data, re-skinned by a palette and a font pairing, returned as an image.
- `create_from_starter(theme=...)`: a palette key, or `{palette, fonts, <theme keys>}`; the new styles light, boxed, striped, bubble and elegant.

## [0.3.0] - 2026-10-26

### Added

- `get_capabilities`: everything a layout can hold (kinds, keys and allowed values, gradients, free vector `path` slots, page background, theme, bundled fonts, limits) and ready path recipes.
- `import_svg(svg=... | path=..., w?, h?)`: converts an SVG into the body of a `path` slot; reads local files up to 1 MB.
- `create_from_starter(style=...)`: the bold starter styles folder, wave, diagonal, sidebar and bigtotal.
- `render_preview(output="png")` also returns the page as an image, so the agent can look at its design; `show=false` returns only the path.

### Security

- `import_svg(path=...)` reads only a regular file named `.svg` (after resolving links) whose content is SVG markup, so a prompt-injected path cannot upload keys or other private files.

### Changed

- The server instructions describe the design workflow: capabilities, starter styles, theme, path slots, preview and iterate.

## [0.2.1] - 2026-10-24

### Fixed

- Docs: the default output folder in the server docstring, and the cabinet button for an agent token ("New agent token").

## [0.2.0] - 2026-10-24

First public release.

### Security

- `render_preview` saves files in the user cache folder (`~/.cache/erp2pdf-studio-mcp`, mode 0700) instead of the shared temp directory, and writes through a private temp file renamed into place, so a symlink planted at the file name is replaced, not written through.

### Added

- Translation tools for PDF and email templates:
  - `get_translations(template_id, kind="pdf"|"email")` lists every static
    string (header and footer parts included for PDF templates; subject and
    body text for email templates) with its translation, a status per
    language, glossary suggestions and missing counts.
  - `set_translations(...)` translates many languages in one call: merge
    (`PATCH`) or replace (`replace=true`, `PUT`), add or remove languages,
    set the source language, fill empty cells from the built-in glossary,
    remove unused strings.
  - Email strings keep their inline tags (`b`, `strong`, `i`, `em`, `u`, `a`,
    `span`, `code`, `br`) as numbered placeholders such as `<1>...</1>` and
    `<2/>`; a translation must keep the same `{{ expressions }}` and tags.
- Email template tools: `list_email_templates`, `get_email_template`,
  `create_email_template`, `update_email_template` (with
  `remove_attachment`), `delete_email_template` and `preview_email` (a saved
  template or a draft subject/body, for one record).
- `timezone` (IANA name) on `get_values`, `render_preview` and
  `preview_email`: datetimes print in the document's timezone.
- `language` on `preview_email`; on `get_values` and `render_preview` it also
  picks the `formatDate` presets `"short"`, `"long"` and `"datetime"`.
- Server instructions describe the translation and email-template workflow,
  `{{ odoo_url }}` for the company logo, and the date presets.

### Changed

- The client validates Odoo model names
  (`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$`, at most 128 characters) and record,
  template, part and email template ids (positive integers) before building
  URL paths, and percent-encodes each path segment. Invalid values raise an
  `invalid_argument` tool error and no request is sent.
- The Template API now requires a token issued with "Allow Template API";
  a token without that scope (such as the Odoo connector's) gets
  `403 token_scope`, which reaches the agent as a tool error. The README
  says to issue a dedicated token.
- The User-Agent is `erp2pdf-studio-mcp/0.2`.

## [0.1.0] - 2026-10-18

Initial version (not published).

### Added

- A stdio MCP server on the official MCP Python SDK (`mcp==2.3.0`) whose
  tools map 1:1 onto the erp2pdf.com Template API (`/api/v1/studio/*`),
  configured with `ERP2PDF_API_KEY`, `ERP2PDF_BASE_URL` and
  `ERP2PDF_OUTPUT_DIR`.
- Odoo data: `list_models`, `get_fields`, `search_records`,
  `get_sample_record`.
- PDF templates: `list_templates`, `get_template`, `create_template`,
  `update_template` (whole layout or `upsert_slots` / `delete_slots` /
  `layout_set`), `delete_template`, `duplicate_template`, `validate_layout`,
  `get_values`, `render_preview` (PDF and PNG saved to `ERP2PDF_OUTPUT_DIR`,
  HTML returned inline), `list_starters`, `create_from_starter`.
- Header and footer parts: `list_parts`, `create_part`, `update_part`,
  `delete_part`.
- API errors become MCP tool errors carrying the API's stable `code`,
  `message` and, for an invalid layout, the `{path, code, message}` list.
- Tests that mock the HTTP layer with `httpx.MockTransport`.

[Unreleased]: https://github.com/c4pointer/erp2pdf-studio-mcp/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/c4pointer/erp2pdf-studio-mcp/releases/tag/v0.4.0
[0.3.0]: https://github.com/c4pointer/erp2pdf-studio-mcp/releases/tag/v0.3.0
[0.2.1]: https://github.com/c4pointer/erp2pdf-studio-mcp/releases/tag/v0.2.1
[0.2.0]: https://github.com/c4pointer/erp2pdf-studio-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/c4pointer/erp2pdf-studio-mcp/commits/v0.2.0
