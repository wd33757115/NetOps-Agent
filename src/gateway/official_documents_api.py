"""Legacy URL compatibility; all operations use the package authorization."""
from fastapi import APIRouter
from src.apps.official_documents.backend.api import router as package_router, session, get_current_user
router = APIRouter()
router.include_router(package_router, prefix="/api/v1/official-documents")
