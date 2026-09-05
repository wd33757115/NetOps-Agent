from django.views.decorators.csrf import csrf_exempt
from ..decorators import require_jwt
from ..sync_async import sync_bff_view
from ..proxy_client import proxy_to_fastapi, proxy_stream_to_fastapi, proxy_binary_to_fastapi
from ._helpers import forward_client_headers, parse_json_body


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_documents(request, path=""):
    # Restrict forwarding to the document namespace; identity comes from JWT.
    if any(part in (".", "..") for part in path.split("/")):
        from ..response import bff_error
        return bff_error("无效路径", 400)
    upstream = "/api/v1/official-documents" + ("/" + path.strip("/") if path else "")
    kwargs = dict(method=request.method, fastapi_path=upstream, request_id=request.bff_request_id,
                  extra_headers=forward_client_headers(request), timeout=320)
    if path.endswith("/download"):
        return await proxy_binary_to_fastapi(**kwargs)
    if path.endswith("/events"):
        return await proxy_stream_to_fastapi(**kwargs)
    return await proxy_to_fastapi(**kwargs, params=request.GET.dict(), data=parse_json_body(request) if request.body else None)
