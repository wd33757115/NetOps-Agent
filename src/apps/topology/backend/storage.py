"""Immutable private evidence; only the owning app serves authenticated downloads."""

import hashlib
import json

from src.platform.device_access.contracts import DeviceAccessError


def backend():
    from src.infrastructure.storage.minio_client import get_minio_storage

    return get_minio_storage()


def put(run_id, target_id, attempt, outputs):
    data = json.dumps(outputs, ensure_ascii=False, sort_keys=True).encode()
    sha = hashlib.sha256(data).hexdigest()
    key = f"apps/topology/{run_id}/{target_id}/{attempt}/{sha}.json"
    if not backend().upload_file(key, data, "application/json"):
        raise DeviceAccessError("evidence_unavailable", "无法保存原始证据")
    if backend().download_file(key) != data:
        raise DeviceAccessError("evidence_unavailable", "原始证据校验失败")
    return key


def get(key):
    if not key.startswith("apps/topology/"):
        raise DeviceAccessError("evidence_unavailable", "证据路径无效")
    data = backend().download_file(key)
    if data is None or hashlib.sha256(data).hexdigest() != key.rsplit("/", 1)[-1][:-5]:
        raise DeviceAccessError("evidence_unavailable", "证据缺失或校验失败")
    return json.loads(data)
