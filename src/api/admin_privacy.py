"""Backend-owned projection for the optional admin privacy boundary.

The mode removes direct account linkage from observability responses and marks
user-uploaded artifacts as content-inaccessible.  It intentionally does not
redact free-form conversation, model, or tool text so operational diagnostics
remain useful.
"""

from typing import Any, Mapping, Optional, Sequence

from config import config


ANONYMOUS_ADMIN_OWNER = "匿名用户"


def project_admin_owner(
    user_id: Optional[str],
    display_name: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return the owner fields permitted in admin observability responses."""
    if config.ADMIN_PRIVACY_MODE and user_id is not None:
        return None, ANONYMOUS_ADMIN_OWNER
    return user_id, display_name


def project_admin_uploaded_files(
    files: Any,
) -> Any:
    """Remove persisted artifact ids and original names from upload references."""
    if (
        not config.ADMIN_PRIVACY_MODE
        or not isinstance(files, Sequence)
        or isinstance(files, (str, bytes))
    ):
        return files

    redacted = []
    for index, file in enumerate(files, start=1):
        if not isinstance(file, Mapping):
            continue
        redacted.append(
            {
                "id": None,
                "filename": f"上传文件 {index}",
                "content_accessible": False,
            }
        )
    return redacted


def admin_artifact_content_accessible(
    source: Optional[str],
    *,
    user_upload_origin: bool = False,
) -> bool:
    """Whether an admin may read artifact content under the active policy."""
    return not (
        config.ADMIN_PRIVACY_MODE
        and (source == "user_upload" or user_upload_origin)
    )


def project_admin_artifact_summary(
    artifact: Mapping[str, Any],
    *,
    redacted_index: int,
) -> dict[str, Any]:
    """Project artifact metadata without exposing upload-derived names or ids."""
    projected = dict(artifact)
    accessible = admin_artifact_content_accessible(
        artifact.get("source"),
        user_upload_origin=bool(artifact.get("user_upload_origin")),
    )
    projected["content_accessible"] = accessible
    if not accessible:
        projected.update(
            {
                "id": f"__redacted_upload_{redacted_index}__",
                "title": f"上传文件 {redacted_index}",
                "original_filename": None,
            }
        )
    return projected
