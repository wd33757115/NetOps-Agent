# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import AsyncMock, patch

import httpx
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve, reverse


class ForwardClientHeadersTests(SimpleTestCase):
    def test_forwards_governed_protocol_headers(self):
        request = RequestFactory().post(
            "/api/patrol/example",
            data={},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-token",
            HTTP_IDEMPOTENCY_KEY="request-key-123",
            HTTP_IF_MATCH='"revision-1"',
            HTTP_X_USER_ID="forged-user",
        )
        request.user = AnonymousUser()

        from bff.views._helpers import forward_client_headers

        headers = forward_client_headers(request)

        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertEqual(headers["Idempotency-Key"], "request-key-123")
        self.assertEqual(headers["If-Match"], '"revision-1"')
        self.assertNotIn("X-User-Id", headers)


class DeleteBodyProxyTests(SimpleTestCase):
    async def test_proxy_client_forwards_delete_json_body(self):
        response = httpx.Response(
            200,
            json={"success": True},
            request=httpx.Request("DELETE", "http://upstream.test/jobs/job-1"),
        )
        client = type(
            "UpstreamClient",
            (),
            {"request": AsyncMock(return_value=response)},
        )()

        with (
            patch("bff.proxy_client._get_client", return_value=client),
            patch(
                "bff.proxy_client._build_url",
                return_value="http://upstream.test/jobs/job-1",
            ),
        ):
            from bff.proxy_client import proxy_request

            await proxy_request(
                method="DELETE",
                path="/jobs/job-1",
                request_id="delete-body-test",
                data={"reason": "scoped cleanup"},
            )

        client.request.assert_awaited_once()
        self.assertEqual(
            client.request.await_args.kwargs["json"],
            {"reason": "scoped cleanup"},
        )

    async def test_parser_proxy_reads_delete_json_body(self):
        request = RequestFactory().delete(
            "/api/parser/jobs/job-1/",
            data={"reason": "operator cleanup"},
            content_type="application/json",
        )
        request.bff_request_id = "parser-delete-test"

        with patch(
            "bff.views.parser.proxy_to_fastapi",
            new_callable=AsyncMock,
        ) as proxy:
            from bff.views.parser import _proxy

            await _proxy(request, "jobs/job-1")

        self.assertEqual(
            proxy.await_args.kwargs["data"],
            {"reason": "operator cleanup"},
        )


class PatrolBatchDownloadRouteTests(SimpleTestCase):
    def test_report_route_resolves_to_binary_proxy(self):
        batch_id = "patrol-batch-test"
        path = reverse("bff_patrol_batch_report", kwargs={"patrol_batch_id": batch_id})

        match = resolve(path)

        self.assertEqual(
            path,
            f"/api/workflows/runs/patrol-batches/{batch_id}/report.md",
        )
        self.assertEqual(match.url_name, "bff_patrol_batch_report")
        self.assertEqual(match.kwargs["patrol_batch_id"], batch_id)

    def test_batch_api_routes_resolve(self):
        batch_id = "patrol-batch-test"
        expected = {
            "bff_patrol_batch_summary": "summary/",
            "bff_patrol_batch_devices": "devices",
            "bff_patrol_batch_check_results": "check-results",
            "bff_patrol_batch_archive": "archive/",
            "bff_patrol_batch_restore": "restore/",
            "bff_patrol_batch_cancel": "cancel/",
            "bff_patrol_batch_rerun": "rerun/",
            "bff_patrol_batch_retry": "retry/",
            "bff_patrol_batch_raw_data": "raw-data.jsonl",
            "bff_patrol_batch_raw_cli": "raw-cli.zip",
            "bff_patrol_batch_events": "events.csv",
            "bff_patrol_batch_leadership_report": "report.docx",
            "bff_patrol_batch_technical_workbook": "report.xlsx",
            "bff_patrol_batch_leadership_report_json": "report.json",
            "bff_patrol_batch_evidence_package": "evidence.zip",
        }

        for route_name, suffix in expected.items():
            path = reverse(route_name, kwargs={"patrol_batch_id": batch_id})
            match = resolve(path)
            self.assertEqual(
                path,
                f"/api/workflows/runs/patrol-batches/{batch_id}/{suffix}",
            )
            self.assertEqual(match.url_name, route_name)

    async def test_binary_proxy_preserves_report_integrity_headers(self):
        response = type(
            "UpstreamResponse",
            (),
            {
                "status_code": 200,
                "content": b"report-bytes",
                "headers": httpx.Headers({
                    "content-type": "application/zip",
                    "content-disposition": 'attachment; filename="evidence.zip"',
                    "x-netops-report-status": "released",
                    "x-netops-report-facts-status": "final",
                    "x-netops-report-source-digest": "a" * 64,
                    "x-netops-report-release-digest": "b" * 64,
                    "x-netops-report-manifest-id": "manifest-v1",
                    "x-netops-report-version": "1",
                    "x-netops-raw-evidence-set-digest": "c" * 64,
                    "x-netops-raw-file-count": "2",
                    "x-untrusted-upstream-header": "must-not-pass",
                }),
            },
        )()
        client = type(
            "UpstreamClient",
            (),
            {"request": AsyncMock(return_value=response)},
        )()

        with patch("bff.proxy_client._get_client", return_value=client):
            from bff.proxy_client import proxy_binary_to_fastapi

            proxied = await proxy_binary_to_fastapi(
                method="GET",
                fastapi_path="/api/v1/workflows/example/evidence.zip",
                request_id="bff-report-header-test",
            )

        self.assertEqual(proxied.content, b"report-bytes")
        self.assertEqual(proxied["X-NetOps-Report-Status"], "released")
        self.assertEqual(proxied["X-NetOps-Report-Facts-Status"], "final")
        self.assertEqual(
            proxied["X-NetOps-Report-Source-Digest"],
            "a" * 64,
        )
        self.assertEqual(
            proxied["X-NetOps-Report-Release-Digest"],
            "b" * 64,
        )
        self.assertEqual(
            proxied["X-NetOps-Report-Manifest-Id"],
            "manifest-v1",
        )
        self.assertEqual(proxied["X-NetOps-Report-Version"], "1")
        self.assertEqual(
            proxied["X-NetOps-Raw-Evidence-Set-Digest"],
            "c" * 64,
        )
        self.assertEqual(proxied["X-NetOps-Raw-File-Count"], "2")
        self.assertNotIn("X-Untrusted-Upstream-Header", proxied.headers)


class OfflinePatrolRouteTests(SimpleTestCase):
    def test_offline_job_collection_route_resolves(self):
        path = reverse("bff_offline_patrol_jobs")
        match = resolve(path)

        self.assertEqual(path, "/api/patrol/offline-jobs/")
        self.assertEqual(match.url_name, "bff_offline_patrol_jobs")

    def test_offline_job_nested_and_artifact_routes_resolve(self):
        for suffix in (
            "offline-test/preview",
            "offline-test/device-bindings/confirm",
            "offline-test/artifacts/raw",
        ):
            path = reverse("bff_offline_patrol_job_path", kwargs={"path": suffix})
            match = resolve(path)
            self.assertEqual(path, f"/api/patrol/offline-jobs/{suffix}/")
            self.assertEqual(match.url_name, "bff_offline_patrol_job_path")


class PatrolGovernanceRouteTests(SimpleTestCase):
    async def test_report_details_uses_binary_proxy(self):
        from bff.views.patrol import _proxy
        request = RequestFactory().get('/api/patrol/offline-jobs/job/report/details')
        request.user = AnonymousUser()
        request.bff_request_id = 'details-test'
        with patch('bff.views.patrol.proxy_binary_to_fastapi', new_callable=AsyncMock) as proxy:
            await _proxy(request, 'job/report/details')
        self.assertEqual(proxy.call_args.kwargs['method'], 'GET')
        self.assertTrue(proxy.call_args.kwargs['fastapi_path'].endswith('/report/details'))

    async def test_report_export_forwards_json_and_binary_response(self):
        from bff.views.patrol import _proxy
        request = RequestFactory().post('/api/patrol/offline-jobs/job/report/export', data='{"bundle":true}', content_type='application/json')
        request.user = AnonymousUser()
        request.bff_request_id = 'report-test'
        with patch('bff.views.patrol.proxy_binary_to_fastapi', new_callable=AsyncMock) as proxy:
            await _proxy(request, 'job/report/export')
        self.assertEqual(proxy.call_args.kwargs['method'], 'POST')
        self.assertEqual(proxy.call_args.kwargs['data'], {'bundle': True})
        self.assertEqual(proxy.call_args.kwargs['fastapi_path'], '/api/v1/patrol/offline-jobs/job/report/export')

    def test_report_manifest_and_risk_routes_use_patrol_proxy(self):
        for suffix in (
            "batches/patrol-batch-test/report-manifest",
            "batches/patrol-batch-test/reports/materialize",
            "risk-cases",
        ):
            path = reverse("bff_patrol_path", kwargs={"path": suffix})
            match = resolve(path)

            self.assertEqual(path, f"/api/patrol/{suffix}")
            self.assertEqual(match.url_name, "bff_patrol_path")

    def test_patrol_proxy_maps_to_versioned_gateway_path(self):
        from bff.views.patrol import _patrol_api_path

        self.assertEqual(
            _patrol_api_path(
                "/batches/patrol-batch-test/report-manifest/"
            ),
            (
                "/api/v1/patrol/batches/"
                "patrol-batch-test/report-manifest"
            ),
        )
