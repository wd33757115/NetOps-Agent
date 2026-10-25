# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""公文写作 Skill 业务模块。"""

from src.apps.official_documents.backend.writing.service import official_document_writing_handler

__all__ = ["official_document_writing_handler"]
