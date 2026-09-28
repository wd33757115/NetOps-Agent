"""Run with yangson==1.6.3 to validate exported data against frozen RFC modules."""

import hashlib
import json
from pathlib import Path

import pytest

from src.apps.topology.backend.normalize import build_graph, rfc8345

ROOT = Path(__file__).resolve().parents[2] / "src/apps/topology/resources/yang"


def test_frozen_yang_checksums():
    for filename, provenance in json.loads((ROOT / "sources.json").read_text()).items():
        assert hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == provenance["sha256"]


def test_export_validates_with_rfc8345_yang():
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    model = yangson.DataModel((ROOT / "library.json").read_text(), [str(ROOT)])
    graph = build_graph(
        "lab-network",
        "lab",
        [
            {
                "id": "evidence-1",
                "address": "10.0.0.1",
                "observed_at": "2026-09-28T00:00:00Z",
                "data": {
                    "neighbors": [
                        {
                            "local_port": "Eth1/1",
                            "chassis_id": "00:11:22:33:44:55",
                            "remote_port": "Eth1/2",
                            "name": "edge",
                            "addresses": ["10.0.0.2"],
                        }
                    ]
                },
            }
        ],
    )
    model.from_raw(rfc8345(graph)).validate()
    broken = rfc8345(graph)
    broken["ietf-network:networks"]["network"][0]["unknown-property"] = "invalid"
    with pytest.raises(Exception):
        model.from_raw(broken).validate()
