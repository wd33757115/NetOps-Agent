# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

import io
import hashlib
import logging
from pathlib import Path
from typing import BinaryIO, Optional

from src.common.config import get_settings

logger = logging.getLogger(__name__)

try:
    from minio import Minio
    from minio.error import S3Error
    MINIO_AVAILABLE = True
except ImportError:
    MINIO_AVAILABLE = False

settings = get_settings()


class MinIOStorage:
    """MinIO S3 兼容文件存储客户端"""

    _instance: Optional["MinIOStorage"] = None
    _client = None
    _bucket_name = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        try:
            self._client = Minio(
                settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE
            )
            self._bucket_name = settings.MINIO_BUCKET_NAME

            if not self._client.bucket_exists(self._bucket_name):
                self._client.make_bucket(self._bucket_name)
                logger.info("MinIO: 创建 Bucket '%s'", self._bucket_name)

            logger.info("MinIO 存储客户端初始化成功")
        except Exception as e:
            logger.warning("MinIO 初始化失败(可稍后启动): %s", e)
            self._client = None

    def is_ready(self) -> bool:
        return self._client is not None

    def upload_file(
        self,
        object_name: str,
        file_data: bytes | BinaryIO,
        content_type: str = "application/octet-stream"
    ) -> bool:
        if not self._client:
            return False

        try:
            if isinstance(file_data, bytes):
                file_data = io.BytesIO(file_data)
                file_len = len(file_data.getvalue())
            else:
                file_data.seek(0, 2)
                file_len = file_data.tell()
                file_data.seek(0)

            self._client.put_object(
                bucket_name=self._bucket_name,
                object_name=object_name,
                data=file_data,
                length=file_len,
                content_type=content_type
            )
            return True
        except S3Error as e:
            logger.warning("MinIO 上传失败: %s", e)
            return False

    def get_presigned_url(
        self,
        object_name: str,
        expires: int = 3600 * 24,
        filename: str | None = None,
    ) -> str | None:
        """生成面向用户的下载链接（BFF 签名代理，非 MinIO 内网预签名）。"""
        from src.infrastructure.storage.download_urls import build_object_download_url

        name = filename or (object_name.rsplit("/", 1)[-1] if object_name else None)
        return build_object_download_url(object_name, filename=name, expires=expires)

    def get_internal_presigned_url(self, object_name: str, expires: int = 3600 * 24) -> str | None:
        """MinIO 原生预签名（仅内网/调试）。"""
        if not self._client:
            return None

        try:
            from datetime import timedelta
            url = self._client.presigned_get_object(
                bucket_name=self._bucket_name,
                object_name=object_name,
                expires=timedelta(seconds=expires),
            )
            return str(url)
        except S3Error as e:
            logger.warning("MinIO 生成预签名URL失败: %s", e)
            return None

    def download_file(self, object_name: str) -> bytes | None:
        if not self._client:
            return None

        try:
            response = self._client.get_object(self._bucket_name, object_name)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except S3Error:
            return None

    def sha256_object(
        self,
        object_name: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> dict[str, int | str] | None:
        """Stream an object once and return its byte size and SHA-256."""

        if not self._client:
            return None
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        try:
            response = self._client.get_object(
                self._bucket_name,
                object_name,
            )
            digest = hashlib.sha256()
            size = 0
            try:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
            finally:
                response.close()
                response.release_conn()
            return {
                "sha256": digest.hexdigest(),
                "size": size,
            }
        except S3Error:
            return None

    def download_to_file(self, object_name: str, destination: str | Path) -> bool:
        """Stream an object to disk without holding a large patrol archive in memory."""
        if not self._client:
            return False
        try:
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._client.fget_object(self._bucket_name, object_name, str(path))
            return True
        except S3Error as exc:
            logger.warning("MinIO download failed: %s", exc)
            return False

    def presigned_put_url(self, object_name: str, expires: int = 3600) -> str | None:
        if not self._client:
            return None
        try:
            from datetime import timedelta
            url = self._client.presigned_put_object(
                bucket_name=self._bucket_name,
                object_name=object_name,
                expires=timedelta(seconds=expires),
            )
            return str(url)
        except S3Error as e:
            logger.warning("MinIO 生成上传预签名URL失败: %s", e)
            return None

    def delete_object(self, object_name: str) -> bool:
        if not self._client:
            return False
        try:
            self._client.remove_object(self._bucket_name, object_name)
            return True
        except S3Error as e:
            logger.warning("MinIO 删除对象失败: %s", e)
            return False

    def list_object_keys(self, prefix: str, *, recursive: bool = True) -> list[str]:
        if not self._client:
            return []
        try:
            objects = self._client.list_objects(
                self._bucket_name,
                prefix=prefix.rstrip("/") + "/" if prefix else "",
                recursive=recursive,
            )
            return [obj.object_name for obj in objects if obj.object_name]
        except S3Error as e:
            logger.warning("MinIO 列举对象失败: %s", e)
            return []

    def delete_objects_by_prefix(self, prefix: str) -> int:
        keys = self.list_object_keys(prefix, recursive=True)
        deleted = 0
        for key in keys:
            if self.delete_object(key):
                deleted += 1
        return deleted

    def stat_object(self, object_name: str) -> dict | None:
        if not self._client:
            return None
        try:
            stat = self._client.stat_object(self._bucket_name, object_name)
            return {
                "size": stat.size,
                "etag": stat.etag,
                "content_type": stat.content_type,
            }
        except S3Error:
            return None

    def copy_object(self, source_key: str, dest_key: str) -> bool:
        if not self._client:
            return False
        try:
            from minio.commonconfig import CopySource
            self._client.copy_object(
                self._bucket_name,
                dest_key,
                CopySource(self._bucket_name, source_key),
            )
            return True
        except S3Error as e:
            logger.warning("MinIO 复制对象失败: %s -> %s: %s", source_key, dest_key, e)
            return False

    @property
    def bucket_name(self) -> str | None:
        return self._bucket_name


def get_minio_storage() -> MinIOStorage:
    return MinIOStorage()


try:
    minio_storage = get_minio_storage()
except Exception:
    pass
