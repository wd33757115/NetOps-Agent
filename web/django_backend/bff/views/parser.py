"""Legacy URLs share package admission and the package proxy."""
from .app_proxy import proxy_app

async def proxy_parser_collection(request, resource):
    return await proxy_app(request, "rule-governance", "parser/" + resource)

async def proxy_parser_action(request, resource, item_id, action):
    return await proxy_app(request, "rule-governance", f"parser/{resource}/{item_id}/{action}")

async def proxy_parser_job_detail(request, job_id):
    return await proxy_app(request, "rule-governance", "parser/jobs/" + job_id)

async def proxy_parser_path(request, path):
    return await proxy_app(request, "rule-governance", "parser/" + path)

for view in (proxy_parser_collection, proxy_parser_action, proxy_parser_job_detail, proxy_parser_path):
    view.csrf_exempt = True
