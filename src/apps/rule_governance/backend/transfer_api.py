"""App-authorized data exchange routes; no source filesystem path is accepted."""
import base64
import binascii
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, OperationalError
from .access import app_user, execution_lock
from .transfer_format import MAX_ZIP
from .transfer_service import TransferService, TransferConflict

router = APIRouter(prefix='/rule-packages', dependencies=[Depends(app_user)])


class ExportRequest(BaseModel):
    selections: list[str] = Field(default_factory=list, max_length=5000)
    all_active: bool = False
    include_drafts: bool = False
    include_samples: bool = False


class PackageRequest(BaseModel):
    content_base64: str = Field(max_length=(MAX_ZIP * 4 // 3 + 4))


class ImportRequest(PackageRequest):
    revision: str = Field(min_length=64, max_length=64)
    confirm_conflicts: bool = False


def decode(body):
    try:
        return base64.b64decode(body.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, '文件编码无效') from exc


def actor(user):
    """Who acted, by the name the rest of the catalog records: drafts and history show it as the initiator."""
    return str(user.username or user.user_id).strip()


def call(fn):
    try:
        return fn()
    except TransferConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (IntegrityError, OperationalError) as exc:
        raise HTTPException(409, '资产关系或版本已变化，请重新预检') from exc
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise HTTPException(400, str(exc)[:2000]) from exc


@router.get('/assets')
def assets(kind: str = 'check_version', status: str = 'active', search: str = Query('', max_length=150),
           offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return call(lambda: TransferService().catalog(kind, status, search, offset, limit))


@router.post('/export')
def export(body: ExportRequest, user=Depends(app_user)):
    with execution_lock():
        return call(lambda: TransferService().export(**body.model_dump(), actor=actor(user)))


@router.post('/preview')
def preview(body: PackageRequest):
    return call(lambda: TransferService().preview(decode(body)))


@router.post('/import')
def execute(body: ImportRequest, user=Depends(app_user)):
    with execution_lock():
        return call(lambda: TransferService().execute(decode(body), body.revision,
                    confirm_conflicts=body.confirm_conflicts, actor=actor(user)))


@router.get('/history')
def history(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    return TransferService().history(offset, limit)


@router.get('/history/{record_id}')
def detail(record_id: str):
    return call(lambda: TransferService().detail(record_id))


@router.post('/history/{record_id}/validate')
def validate(record_id: str, user=Depends(app_user)):
    with execution_lock():
        return call(lambda: TransferService().validate_import(record_id, actor(user)))
