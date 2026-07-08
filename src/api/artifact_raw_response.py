"""Shared raw-blob response helpers for artifact routes."""

import re
from urllib.parse import quote

from fastapi.responses import Response

from utils.mime import is_safe_inline_image_mime

RAW_ARTIFACT_RESPONSES = {
    200: {
        "content": {
            # The handler returns the blob's TRUE content_type -- image/png,
            # image/jpeg, application/pdf, the docx OOXML MIME, the octet-
            # stream fallback, and arbitrary sandbox-written types.
            # `*/*` covers any media type without enumerating a drift-prone
            # list; schema type=string/format=binary types the body as binary.
            "*/*": {"schema": {"type": "string", "format": "binary"}},
        },
        "description": "Raw artifact blob (image inline, else attachment).",
    },
}


def build_artifact_blob_response(blob: dict) -> Response:
    """Return a raw artifact blob with consistent download/cache headers."""
    content_type = blob["content_type"] or "application/octet-stream"
    disposition = "inline" if is_safe_inline_image_mime(content_type) else "attachment"

    filename = blob["filename"].replace("/", "-").replace("\\", "-")
    # RFC 5987: filename* for non-ASCII, with sanitized ASCII fallback.
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    ascii_fallback = re.sub(r'["\x00-\x1f\x7f]', "_", ascii_fallback)
    utf8_encoded = quote(filename, safe="")
    return Response(
        content=blob["data"],
        media_type=content_type,
        headers={
            "Content-Disposition": (
                f'{disposition}; filename="{ascii_fallback}"; '
                f"filename*=UTF-8''{utf8_encoded}"
            ),
            # Blob bytes can be replaced in place while URL/version stay stable.
            "Cache-Control": "no-cache",
        },
    )
