"""Backend-owned projection for the optional admin privacy boundary.

The mode removes direct account linkage from observability responses and makes
all artifacts content-inaccessible through admin APIs.  It intentionally does
not redact free-form conversation, model, or tool text so operational
diagnostics remain useful.
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


def project_admin_referenced_artifacts(
    files: Any,
) -> Any:
    """Remove ids and original names from existing-file references."""
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
                "filename": f"引用文件 {index}",
                "content_accessible": False,
            }
        )
    return redacted


def admin_artifact_content_accessible() -> bool:
    """Whether an admin may read artifact content under the active policy."""
    return not config.ADMIN_PRIVACY_MODE


def project_admin_artifact_summary(
    artifact: Mapping[str, Any],
    *,
    protected_index: int,
) -> dict[str, Any]:
    """Project artifact metadata without exposing protected names or ids."""
    projected = dict(artifact)
    accessible = admin_artifact_content_accessible()
    projected["content_accessible"] = accessible
    if not accessible:
        projected.update(
            {
                "id": f"__protected_artifact_{protected_index}__",
                "title": f"受保护文件 {protected_index}",
                "original_filename": None,
            }
        )
    return projected
