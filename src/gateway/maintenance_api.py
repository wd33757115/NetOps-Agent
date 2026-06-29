"""System maintenance API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.auth.dependencies import require_role
from src.infrastructure.db.reset import RESET_CONFIRMATION, reset_business_data

router = APIRouter(
    prefix="/api/v1/maintenance",
    tags=["System Maintenance"],
    dependencies=[Depends(require_role(["admin"]))],
)


class DatabaseResetRequest(BaseModel):
    dry_run: bool = True
    confirmation: str | None = None
    include_configuration: bool = False


@router.get("/database-reset/preview/")
def preview_database_reset():
    return reset_business_data(dry_run=True).to_dict()


@router.post("/database-reset/")
def run_database_reset(payload: DatabaseResetRequest):
    try:
        return reset_business_data(
            dry_run=payload.dry_run,
            confirmation=payload.confirmation,
            include_configuration=payload.include_configuration,
        ).to_dict()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "required_confirmation": RESET_CONFIRMATION,
            },
        ) from exc
