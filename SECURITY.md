# Security Policy

## Supported versions

Security fixes are made for the latest released version. Please upgrade to it
before reporting.

| Version | Supported |
|---|---|
| 0.2.x | yes |
| < 0.2 | no |

## Reporting a vulnerability

Please report security issues privately by email to **hello@erp2pdf.com**,
not in a public GitHub issue, discussion or pull request.

Include:

- a description of the issue and its impact;
- steps to reproduce, or a proof of concept;
- the version of erp2pdf-studio-mcp, the Python version and the MCP client
  you use.

We aim to acknowledge a report within 3 business days and will keep you
informed while we work on a fix. Please give us reasonable time to release a
fix before you disclose the issue publicly. We are happy to credit you in the
release notes if you wish.

Issues in the erp2pdf.com service itself (the Template API, the cabinet,
billing) can be reported to the same address.

## Never post tokens

Your erp2pdf.com API token gives access to your account's templates. Never
paste a token into an issue, a pull request, a log excerpt, a screenshot or a
chat with other people. Redact it (`Authorization: Bearer <redacted>`) before
sharing any output.

If a token has been exposed, revoke it at once in the erp2pdf.com cabinet
(API tokens) and issue a new one. Use a dedicated token for this server,
issued with "Allow Template API", and never reuse the Odoo connector's
token, so that revoking one does not affect the other.

## How the server handles the token

- It reads the token from the `ERP2PDF_API_KEY` environment variable only.
- It sends the token only as an `Authorization: Bearer` header, and only to
  `ERP2PDF_BASE_URL` (default `https://erp2pdf.com`), never in a URL.
- It validates model names and ids before they become URL path segments, so a
  tool argument cannot redirect a request to a different API route.
- Rendered previews are written to `ERP2PDF_OUTPUT_DIR` (default: a folder in
  the system temp directory). Point it at a private folder if other users
  share the machine.
