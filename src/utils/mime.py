"""MIME helpers shared by artifact serving paths."""

SAFE_INLINE_IMAGE_MIMES = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
})


def is_safe_inline_image_mime(content_type: str | None) -> bool:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return mime in SAFE_INLINE_IMAGE_MIMES
