"""Model-facing skill guidance shared by every activation entry point.

Selecting a skill in the composer and calling ``read_skill`` are two ways to
activate the same capability.  Both must therefore expose the same SKILL.md
body and the same conditional instruction about bundled files.  Keeping that
rendering here makes a semantic mismatch between the two paths unrepresentable.
"""

from tools.base import MOUNT_SKILL_NAME

_MOUNT_HINT_EXTRA_FILES = (
    "\n\n---\n"
    "Above is this skill's guidance (SKILL.md). It bundles more files "
    "(references/, scripts/, assets/) that are NOT shown here — call mount_skill "
    "to unpack them into the sandbox, then read or run them with bash."
)

_MOUNT_HINT_NO_EXTRA_FILES = (
    "\n\n---\n"
    "Above is this skill's complete guidance (SKILL.md); it has no bundled files."
)


_MOUNT_HINT_EXTRA_FILES_UNAVAILABLE = (
    "\n\n---\n"
    "Above is this skill's guidance (SKILL.md). It bundles more files "
    "(references/, scripts/, assets/) that are NOT shown here, but this agent "
    "does not have the sandbox capabilities required to access them. Do not try "
    "to mount or run those files; use only the guidance shown above, or ask the "
    "caller to hand the task to an agent with sandbox access."
)


def can_access_skill_bundle(effective_toolset, slug: str) -> bool:
    """Whether mount_skill + bash are callable after activating ``slug``."""
    if effective_toolset is None:
        return False
    grant = getattr(effective_toolset, "skill_grants", {}).get(slug)
    granted = getattr(grant, "permissions", {}) if grant is not None else {}
    return all(
        name in effective_toolset or name in granted
        for name in (MOUNT_SKILL_NAME, "bash")
    )


def render_skill_guidance(
    body: str, *, has_extra_files: bool, bundle_accessible: bool
) -> str:
    """Return the complete guidance shown to the model for one activation."""
    if not has_extra_files:
        hint = _MOUNT_HINT_NO_EXTRA_FILES
    elif bundle_accessible:
        hint = _MOUNT_HINT_EXTRA_FILES
    else:
        hint = _MOUNT_HINT_EXTRA_FILES_UNAVAILABLE
    return body + hint
