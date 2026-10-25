"""Private immutable blobs. Never issue bearerless object URLs."""

import hashlib
import io
import json
import zipfile
from pathlib import PurePath

from fastapi import HTTPException

PREFIX = "apps/firewall-policy/"
MAX_UPLOAD = 10 * 1024 * 1024
SUFFIXES = {
    "policy": {".xlsx"},
    "topology": {".json"},
    "block": {".xlsx", ".csv"},
    "whitelist": {".xlsx", ".csv"},
}


def storage():
    from src.infrastructure.storage.minio_client import get_minio_storage

    return get_minio_storage()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def put(key, data, content_type="application/octet-stream"):
    if not key.startswith(PREFIX) or not storage().upload_file(key, data, content_type):
        raise HTTPException(503, "文件保存失败，请稍后重试")
    if storage().download_file(key) != data:
        raise HTTPException(503, "文件保存校验失败，请稍后重试")


def get(key, sha):
    data = storage().download_file(key) if key.startswith(PREFIX) else None
    if data is None or digest(data) != sha:
        raise HTTPException(503, "文件缺失或校验失败，请联系管理员恢复备份")
    return data


def validate(filename, kind, data):
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(413, "文件应大于 0 字节且不超过 10 MB")
    if (
        not filename
        or len(filename) > 255
        or "/" in filename
        or "\\" in filename
        or any(ord(c) < 32 for c in filename)
    ):
        raise HTTPException(422, "文件名无效")
    suffix = PurePath(filename).suffix.lower()
    if suffix not in SUFFIXES.get(kind, ()):
        raise HTTPException(
            422,
            "封禁名单和白名单须为 XLSX 或 CSV"
            if kind in {"block", "whitelist"}
            else "策略文件须为 XLSX，拓扑文件须为 JSON",
        )
    try:
        if suffix == ".json":
            if not isinstance(json.loads(data), dict):
                raise ValueError("拓扑应为 JSON 对象")
        elif suffix == ".csv":
            if not any(_decodes(data, encoding) for encoding in ("utf-8-sig", "gb18030")):
                raise ValueError("CSV 须为 UTF-8 或 GBK 编码")
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(x.file_size for x in entries) > 64 * 1024 * 1024:
                    raise ValueError("工作簿解压后过大")
                if "xl/workbook.xml" not in archive.namelist():
                    raise ValueError("无效工作簿")
                if any(x.flag_bits & 1 for x in entries):
                    raise ValueError("不支持加密工作簿")
    except (ValueError, UnicodeError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, "文件格式无效或内容超出限制") from exc


def _decodes(data, encoding):
    try:
        data.decode(encoding)
    except UnicodeDecodeError:
        return False
    return True
