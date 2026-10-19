"""Count lines, words and characters of a text and write a report.

Called by the platform as: python scripts/run.py --params params.json --output-dir <dir>
The last line on standard output is the result JSON.
"""

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    params = json.loads(Path(args.params).read_text(encoding="utf-8"))

    text = str(params.get("text") or "")
    if not text.strip():
        print(json.dumps({"success": False, "error": "text is empty"}, ensure_ascii=False))
        return 0
    lines, words, chars = len(text.splitlines()), len(text.split()), len(text)
    summary = f"{lines} 行，{words} 个词，{chars} 个字符"

    if args.output_dir:
        title = str(params.get("title") or "文本统计")
        report = f"# {title}\n\n- 行数：{lines}\n- 词数：{words}\n- 字符数：{chars}\n"
        Path(args.output_dir, "report.md").write_text(report, encoding="utf-8")

    print(json.dumps({"success": True, "summary": summary, "lines": lines, "words": words, "chars": chars},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
