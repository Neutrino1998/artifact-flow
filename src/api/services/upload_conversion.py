"""Pure upload admission and conversion shared by conversation endpoints."""

from dataclasses import dataclass

from fastapi import HTTPException, UploadFile

from config import config
from utils.doc_converter import DocConverter
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


@dataclass
class ConvertedUpload:
    filename: str
    content: str
    content_type: str
    metadata: dict
    blob: bytes | None = None


async def convert_uploaded_file(file: UploadFile) -> ConvertedUpload:
    """Size-check and convert one upload without performing DB writes."""
    max_mb = config.MAX_UPLOAD_SIZE / 1024 / 1024
    if file.size is not None and file.size > config.MAX_UPLOAD_SIZE:
        detail = (
            f"File too large: {file.size / 1024 / 1024:.1f}MB (max {max_mb:.0f}MB)"
        )
        logger.warning("Upload rejected (422) for %r: %s", file.filename, detail)
        raise HTTPException(status_code=422, detail=detail)
    file_bytes = await file.read()
    if len(file_bytes) > config.MAX_UPLOAD_SIZE:
        detail = (
            f"File too large: {len(file_bytes) / 1024 / 1024:.1f}MB "
            f"(max {max_mb:.0f}MB)"
        )
        logger.warning("Upload rejected (422) for %r: %s", file.filename, detail)
        raise HTTPException(status_code=422, detail=detail)

    try:
        result = await DocConverter().convert(
            file_bytes, file.filename or "untitled"
        )
    except ValueError as exc:
        logger.warning("Upload rejected (422) for %r: %s", file.filename, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("File conversion failed for %r: %s", file.filename, exc)
        detail = str(exc) if config.DEBUG else "Internal server error"
        raise HTTPException(status_code=500, detail=detail) from exc

    return ConvertedUpload(
        filename=file.filename or "untitled",
        content=result.content,
        content_type=result.content_type,
        metadata=result.metadata or {},
        blob=result.blob,
    )
