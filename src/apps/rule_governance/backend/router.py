from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError

from .access import app_user
from .api import router as parser
from .source_api import router as sources
from .transfer_api import router as transfers

router = APIRouter()
router.include_router(transfers)
router.include_router(sources, prefix="/parser/asset-source-jobs")
router.include_router(parser)
