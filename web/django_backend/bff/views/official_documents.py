"""Compatibility URL; uses the exact same proxy and application admission."""
from .app_proxy import proxy_app

async def proxy_documents(request, path=""):
    return await proxy_app(request, app_id="official-documents", path=path)

proxy_documents.csrf_exempt = True
