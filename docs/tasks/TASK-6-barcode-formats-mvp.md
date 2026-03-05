# Task #6 — Define Barcode Formats for MVP

Generated: 2026-03-05 06:18:03
Status: Completed
Owner: Aran

## Objective
Define the minimum barcode format scope for MVP to maximize usefulness while keeping implementation fast.

## Decision
MVP will support:
1. **QR Code** (2D) — primary format for links, text, and app deep links.
2. **Code 128** (1D) — broad compatibility for logistics/internal labels.
3. **EAN-13** (1D) — retail product code compatibility.

Deferred (Post-MVP):
- PDF417
- Data Matrix
- UPC-A/UPC-E
- Aztec

## Payload Rules (MVP)
- UTF-8 text input
- Max payload:
  - QR: 1024 chars (soft limit)
  - Code128/EAN-13: validated per standard constraints
- Basic input sanitization and trimming before generation

## API Contract (proposed)
`POST /api/barcodes/generate`

Request:
```json
{"format":"qr|code128|ean13","value":"...","size":256,"foreground":"#000000","background":"#ffffff"}
```

Response:
```json
{"ok":true,"format":"qr","image_url":"/generated/<id>.png"}
```

## Acceptance Criteria
- User can choose QR, Code128, or EAN-13.
- Invalid payload returns clear validation error.
- PNG export supported for all MVP formats.
- SVG export supported for QR + Code128 (EAN-13 optional in phase 2).

## Rationale
This mix gives high utility for web/share and basic business operations, while minimizing implementation complexity and QA surface.
