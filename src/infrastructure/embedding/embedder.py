# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""共享 BGE Embedding（Skill Catalog / Router 单例）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.common.config import get_settings

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: SentenceTransformer | None = None
_model_name: str | None = None


def get_embedder():
    """懒加载 SentenceTransformer（进程内单例）。"""
    global _model, _model_name
    settings = get_settings()
    model_name = settings.EMBEDDING_MODEL
    if _model is not None and _model_name == model_name:
        return _model
    try:
        from sentence_transformers import SentenceTransformer

        device = settings.EMBEDDING_DEVICE or "cpu"
        _model = SentenceTransformer(model_name, device=device)
        _model_name = model_name
        logger.info("embedding_model_loaded model=%s device=%s", model_name, device)
        return _model
    except Exception as exc:
        logger.warning("embedding_model_load_failed: %s", exc)
        return None


def encode_text(text: str) -> list[float]:
    model = get_embedder()
    if model is None:
        return []
    try:
        return model.encode(text, normalize_embeddings=True).tolist()
    except Exception as exc:
        logger.warning("encode_text failed: %s", exc)
        return []


def encode_batch(texts: list[str]) -> list[list[float]]:
    model = get_embedder()
    if model is None or not texts:
        return []
    try:
        vectors = model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]
    except Exception as exc:
        logger.warning("encode_batch failed: %s", exc)
        return []


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    if not vec1 or not vec2:
        return 0.0
    import numpy as np

    a = np.array(vec1, dtype=float)
    b = np.array(vec2, dtype=float)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
