#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
公文 DOCX 渲染 CLI。

用法:
  python generate_official_document.py --json document.json --output out.docx
  echo '{...}' | python generate_official_document.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.skills.official_document.render import render_document_bytes
from src.skills.official_document.schema import OfficialDocumentJSON


def main() -> int:
    parser = argparse.ArgumentParser(description="根据结构化 JSON 生成公文 DOCX")
    parser.add_argument("--json", help="JSON 文件路径")
    parser.add_argument("--output", "-o", help="输出 DOCX 路径")
    args = parser.parse_args()

    if args.json:
        raw = Path(args.json).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()

    payload = json.loads(raw)
    document = OfficialDocumentJSON.model_validate(payload)
    docx_bytes = render_document_bytes(document)

    if args.output:
        Path(args.output).write_bytes(docx_bytes)
        print(f"已写入: {args.output}")
    else:
        sys.stdout.buffer.write(docx_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
