"""Model-facing skill guidance shared by every activation entry point.

Selecting a skill in the composer and calling ``read_skill`` are two ways to
activate the same capability.  Both must therefore expose the same SKILL.md
body and the same conditional instruction about bundled files.  Keeping that
rendering here makes a semantic mismatch between the two paths unrepresentable.
"""

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


def render_skill_guidance(body: str, *, has_extra_files: bool) -> str:
    """Return the complete guidance shown to the model for one activation."""
    hint = (
        _MOUNT_HINT_EXTRA_FILES
        if has_extra_files
        else _MOUNT_HINT_NO_EXTRA_FILES
    )
    return body + hint
