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
