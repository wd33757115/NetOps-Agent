# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt, require_role
from ..proxy_client import TASK_TIMEOUT, proxy_binary_to_fastapi, proxy_to_fastapi
from ..sync_async import sync_bff_view
from ._helpers import forward_client_headers, parse_json_body

logger = logging.getLogger("bff.views.workflows")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_templates(request: HttpRequest) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "GET":
        return await proxy_to_fastapi(
            method="GET",
            fastapi_path="/api/v1/workflows/templates",
            request_id=request.bff_request_id,
            extra_headers=headers,
        )
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/templates",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_template_dsl(request: HttpRequest, name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/templates/{name}/dsl",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_template_detail(request: HttpRequest, name: str) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "GET":
        return await proxy_to_fastapi(
            method="GET",
            fastapi_path=f"/api/v1/workflows/templates/{name}",
            request_id=request.bff_request_id,
            extra_headers=headers,
        )
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="PUT",
        fastapi_path=f"/api/v1/workflows/templates/{name}",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_reload(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/reload",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_validate(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/validate",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_preview(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/preview",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_generate(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/generate",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_infer_mappings(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/infer-mappings",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_categories(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/api/v1/workflows/categories",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_expression_hints_preview(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/expression-hints/preview",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_chat_intent_preview(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/chat-intent/preview",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_dry_run(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/dry-run",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_chat_intent_suggest_nl(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/chat-intent/suggest-nl",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_plugins(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/api/v1/workflows/plugins",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_import(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/import",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_plugin_versions(request: HttpRequest, name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/plugins/{name}/versions",
        request_id=request.bff_request_id,
        params=dict(request.GET.items()),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_plugin_version_diff(request: HttpRequest, name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/plugins/{name}/versions/diff",
        request_id=request.bff_request_id,
        params=dict(request.GET.items()),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_plugin_export(request: HttpRequest, name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/plugins/{name}/export",
        request_id=request.bff_request_id,
        params=dict(request.GET.items()),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_plugin_submit_review(request: HttpRequest, name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/workflows/plugins/{name}/submit-review",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@require_role("admin")
@sync_bff_view
async def proxy_plugin_publish(request: HttpRequest, name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/workflows/plugins/{name}/publish",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@require_role("admin")
@sync_bff_view
async def proxy_plugin_reject(request: HttpRequest, name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/workflows/plugins/{name}/reject",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@require_role("admin", "operator")
@sync_bff_view
async def proxy_plugin_delete(request: HttpRequest, name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="DELETE",
        fastapi_path=f"/api/v1/workflows/plugins/{name}",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_plugin_publish_market(request: HttpRequest, name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/workflows/plugins/{name}/publish-to-market",
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_market_templates(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/api/v1/workflows/market/templates",
        request_id=request.bff_request_id,
        params=dict(request.GET.items()),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_market_template_detail(request: HttpRequest, template_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/market/templates/{template_id}",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_runs(request: HttpRequest) -> JsonResponse:
    headers = forward_client_headers(request)
    params = dict(request.GET.items())
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/api/v1/workflows/runs",
        request_id=request.bff_request_id,
        params=params,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batches(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/api/v1/workflows/runs/patrol-batches",
        request_id=request.bff_request_id,
        params=dict(request.GET.items()),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_summary(
    request: HttpRequest, patrol_batch_id: str
) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/summary",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_devices(
    request: HttpRequest, patrol_batch_id: str
) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/devices",
        request_id=request.bff_request_id,
        params=dict(request.GET.items()),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_check_results(
    request: HttpRequest, patrol_batch_id: str
) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=(
            f"/api/v1/workflows/runs/patrol-batches/"
            f"{patrol_batch_id}/check-results"
        ),
        request_id=request.bff_request_id,
        params=dict(request.GET.items()),
        extra_headers=forward_client_headers(request),
    )


async def _proxy_patrol_batch_action(
    request: HttpRequest,
    patrol_batch_id: str,
    action: str,
) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/{action}",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_archive(request: HttpRequest, patrol_batch_id: str) -> JsonResponse:
    return await _proxy_patrol_batch_action(request, patrol_batch_id, "archive")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_restore(request: HttpRequest, patrol_batch_id: str) -> JsonResponse:
    return await _proxy_patrol_batch_action(request, patrol_batch_id, "restore")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_cancel(request: HttpRequest, patrol_batch_id: str) -> JsonResponse:
    return await _proxy_patrol_batch_action(request, patrol_batch_id, "cancel")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_rerun(request: HttpRequest, patrol_batch_id: str) -> JsonResponse:
    return await _proxy_patrol_batch_action(request, patrol_batch_id, "rerun")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_retry(request: HttpRequest, patrol_batch_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/retry",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_export(request: HttpRequest, patrol_batch_id: str):
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/export.csv",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_raw_data(request: HttpRequest, patrol_batch_id: str):
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/raw-data.jsonl",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_raw_cli(request: HttpRequest, patrol_batch_id: str):
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/raw-cli.zip",
        request_id=request.bff_request_id,
        params=request.GET.dict(),
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_events(request: HttpRequest, patrol_batch_id: str):
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/events.csv",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_report(request: HttpRequest, patrol_batch_id: str):
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.md",
        request_id=request.bff_request_id,
        params=request.GET.dict(),
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_leadership_report(
    request: HttpRequest, patrol_batch_id: str
):
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=(
            f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.docx"
        ),
        request_id=request.bff_request_id,
        params=request.GET.dict(),
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_technical_workbook(
    request: HttpRequest, patrol_batch_id: str
):
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=(
            f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.xlsx"
        ),
        request_id=request.bff_request_id,
        params=request.GET.dict(),
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_leadership_report_json(
    request: HttpRequest, patrol_batch_id: str
):
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=(
            f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.json"
        ),
        request_id=request.bff_request_id,
        params=request.GET.dict(),
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_evidence_package(
    request: HttpRequest, patrol_batch_id: str
):
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=(
            f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/evidence.zip"
        ),
        request_id=request.bff_request_id,
        params=request.GET.dict(),
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@require_role("admin")
@sync_bff_view
async def proxy_workflow_test_run(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/workflows/runs/test",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_timeline(request: HttpRequest, run_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/{run_id}/timeline",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_events_stream(request: HttpRequest, run_id: str):
    from ..proxy_client import proxy_stream_to_fastapi

    return await proxy_stream_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/{run_id}/events/stream",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_retry(request: HttpRequest, run_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/workflows/{run_id}/retry",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_workflow_detail(request: HttpRequest, run_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/workflows/{run_id}",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )
