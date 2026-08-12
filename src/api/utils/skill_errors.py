"""HTTP mapping for skill-management application errors."""

from fastapi import HTTPException

from core.management.skill_manager import SkillManagerError, SkillValidationError


def map_skill_error(exc: SkillManagerError) -> HTTPException:
    if isinstance(exc, SkillValidationError):
        return HTTPException(
            status_code=exc.status_code,
            detail={
                "message": str(exc),
                "findings": [
                    {
                        "rule": finding.rule,
                        "severity": finding.severity,
                        "message": finding.message,
                    }
                    for finding in exc.findings
                ],
            },
        )
    return HTTPException(
        status_code=getattr(exc, "status_code", 400), detail=str(exc)
    )
