# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill 缓存模块

提供 LRU 缓存功能，用于：
1. Skill 指令内容缓存
2. Embedding 向量缓存
3. 路由结果缓存
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    """缓存条目"""
    key: str
    value: Any
    created_at: float
    accessed_at: float
    hit_count: int = 0


class LRUCache:
    """
    LRU 缓存实现

    特性：
    1. 线程安全
    2. LRU 淘汰策略
    3. TTL 过期机制
    4. 访问统计
    """

    def __init__(
        self,
        max_size: int = 100,
        ttl_seconds: int | None = None,
        name: str = "default"
    ):
        """
        初始化缓存

        Args:
            max_size: 最大缓存条目数
            ttl_seconds: TTL 过期时间（秒），None 表示永不过期
            name: 缓存名称（用于日志）
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.name = name
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        """
        获取缓存值

        Args:
            key: 缓存键

        Returns:
            缓存值，如果不存在或过期返回 None
        """
        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return None

            # 检查 TTL
            if self.ttl_seconds:
                if time.time() - entry.created_at > self.ttl_seconds:
                    del self._cache[key]
                    self._misses += 1
                    return None

            # 更新访问时间
            entry.accessed_at = time.time()
            entry.hit_count += 1

            # 移到末尾（最近使用）
            self._cache.move_to_end(key)

            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any):
        """
        设置缓存值

        Args:
            key: 缓存键
            value: 缓存值
        """
        with self._lock:
            now = time.time()

            # 如果 key 存在，更新值
            if key in self._cache:
                self._cache[key].value = value
                self._cache[key].accessed_at = now
                self._cache.move_to_end(key)
                return

            # 如果缓存满，删除最老的条目
            if len(self._cache) >= self.max_size:
                # 删除最老的（第一个）
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

            # 添加新条目
            self._cache[key] = CacheEntry(
                key=key,
                value=value,
                created_at=now,
                accessed_at=now
            )

    def delete(self, key: str) -> bool:
        """
        删除缓存条目

        Args:
            key: 缓存键

        Returns:
            bool: 是否删除成功
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self):
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0

            return {
                "name": self.name,
                "size": len(self._cache),
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.2%}",
                "entries": [
                    {
                        "key": e.key,
                        "created_at": e.created_at,
                        "accessed_at": e.accessed_at,
                        "hit_count": e.hit_count
                    }
                    for e in list(self._cache.values())[:10]  # 只返回前 10 个
                ]
            }


class SkillCache:
    """
    Skill 专用缓存

    提供三种缓存：
    1. 元数据缓存
    2. 指令内容缓存
    3. Embedding 缓存
    """

    def __init__(
        self,
        max_metadata: int = 200,
        max_instructions: int = 50,
        max_embeddings: int = 500,
        ttl_seconds: int = 1800  # 30 分钟
    ):
        """
        初始化缓存

        Args:
            max_metadata: 元数据缓存大小
            max_instructions: 指令内容缓存大小
            max_embeddings: Embedding 缓存大小
            ttl_seconds: TTL 过期时间
        """
        self.metadata_cache = LRUCache(
            max_size=max_metadata,
            ttl_seconds=ttl_seconds * 2,  # 元数据缓存时间更长
            name="metadata"
        )

        self.instructions_cache = LRUCache(
            max_size=max_instructions,
            ttl_seconds=ttl_seconds,
            name="instructions"
        )

        self.embedding_cache = LRUCache(
            max_size=max_embeddings,
            ttl_seconds=ttl_seconds,
            name="embedding"
        )

    def get_metadata(self, skill_name: str) -> Any | None:
        """获取元数据缓存"""
        return self.metadata_cache.get(skill_name)

    def set_metadata(self, skill_name: str, metadata: Any):
        """设置元数据缓存"""
        self.metadata_cache.set(skill_name, metadata)

    def get_instructions(self, skill_name: str) -> str | None:
        """获取指令缓存"""
        return self.instructions_cache.get(skill_name)

    def set_instructions(self, skill_name: str, instructions: str):
        """设置指令缓存"""
        self.instructions_cache.set(skill_name, instructions)

    def get_embedding(self, text: str) -> list[float] | None:
        """获取 Embedding 缓存"""
        key = self._hash_text(text)
        return self.embedding_cache.get(key)

    def set_embedding(self, text: str, embedding: list[float]):
        """设置 Embedding 缓存"""
        key = self._hash_text(text)
        self.embedding_cache.set(key, embedding)

    def _hash_text(self, text: str) -> str:
        """计算文本哈希"""
        return hashlib.md5(text.encode()).hexdigest()

    def clear_all(self):
        """清空所有缓存"""
        self.metadata_cache.clear()
        self.instructions_cache.clear()
        self.embedding_cache.clear()

    def get_stats(self) -> dict[str, Any]:
        """获取所有缓存统计"""
        return {
            "metadata": self.metadata_cache.get_stats(),
            "instructions": self.instructions_cache.get_stats(),
            "embedding": self.embedding_cache.get_stats()
        }
