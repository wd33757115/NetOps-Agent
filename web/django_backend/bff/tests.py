# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from django.test import SimpleTestCase
from django.urls import resolve, reverse


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
            "bff_patrol_batch_archive": "archive/",
            "bff_patrol_batch_restore": "restore/",
            "bff_patrol_batch_rerun": "rerun/",
            "bff_patrol_batch_retry": "retry/",
            "bff_patrol_batch_raw_data": "raw-data.jsonl",
            "bff_patrol_batch_raw_cli": "raw-cli.zip",
            "bff_patrol_batch_events": "events.csv",
            "bff_patrol_batch_leadership_report": "report.docx",
            "bff_patrol_batch_leadership_report_json": "report.json",
        }

        for route_name, suffix in expected.items():
            path = reverse(route_name, kwargs={"patrol_batch_id": batch_id})
            match = resolve(path)
            self.assertEqual(
                path,
                f"/api/workflows/runs/patrol-batches/{batch_id}/{suffix}",
            )
            self.assertEqual(match.url_name, route_name)


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
