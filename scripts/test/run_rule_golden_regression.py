"""运行 LLM 规则生成质量回归（golden dataset）。

模型或 Prompt 变更后运行本脚本，对比生成候选是否仍满足 golden 用例的
质量期望（阈值区间、事件规则覆盖、契约主键等）。需要配置 LLM 凭据。

用法:
    python scripts/test/run_rule_golden_regression.py [--cases PATH] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.rule_control.golden_regression import (  # noqa: E402
    load_golden_cases,
    run_golden_regression,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rule generation golden regression")
    parser.add_argument("--cases", default=None, help="golden cases YAML 路径（默认内置 v1）")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 报告")
    args = parser.parse_args()

    cases = load_golden_cases(args.cases)
    report = run_golden_regression(cases=cases)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["results"]:
            status = "PASS" if item["passed"] else "FAIL"
            print(f"[{status}] {item['case_id']} ({item['stage']})")
            for failure in item.get("failures") or []:
                print(f"    - {failure}")
        print(
            f"\ngolden regression: {report['passed']}/{report['total']} passed"
        )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
