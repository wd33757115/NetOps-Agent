"""LLM 规则生成质量回归（golden dataset）。

确定性校验 + 历史回放能挡住"错的"候选，但挡不住"平庸的"（例如阈值全部
设成宽松值导致永不告警）。本模块加载 golden 用例（标准答案期望），对生成
候选做质量断言，供模型/Prompt 变更时的回归对比使用：

    python scripts/test/run_rule_golden_regression.py

评估逻辑本身是确定性的、可单测的；只有真正调用 LLM 生成候选时才需要
`PARSER_LLM_GENERATION_ENABLED` 环境。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

GOLDEN_CASES_PATH = Path(__file__).parent / "assets" / "golden_cases.v1.yaml"


def load_golden_cases(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path else GOLDEN_CASES_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    cases = list(data.get("cases") or [])
    for case in cases:
        if not case.get("id") or not case.get("stage") or not case.get("context"):
            raise ValueError(f"golden case missing id/stage/context: {case!r}")
    return cases


def evaluate_candidate(case: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Deterministically score one generated candidate against expectations."""
    stage = str(case.get("stage") or "")
    expectations = dict(case.get("expectations") or {})
    failures: list[str] = []
    if stage == "change_policy":
        failures.extend(_check_change_policy(expectations, candidate))
    elif stage == "event_rules":
        failures.extend(_check_event_rules(expectations, candidate))
    elif stage == "observation_contract":
        failures.extend(_check_observation_contract(expectations, candidate))
    else:
        failures.append(f"unsupported stage: {stage}")
    return {
        "case_id": case.get("id"),
        "stage": stage,
        "passed": not failures,
        "failures": failures,
    }


def run_golden_regression(
    *,
    provider: Any | None = None,
    cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate a candidate per golden case and evaluate it.

    `provider` must implement `propose(stage=..., context=..., attempt=...)`
    returning an object with a `candidate` dict (see
    RuleGenerationCandidateProvider). Defaults to the LLM provider.
    """
    if provider is None:
        from src.apps.rule_governance.backend.rule_control.generation_agent import (
            DeepSeekRuleGenerationProvider,
        )

        provider = DeepSeekRuleGenerationProvider()
    results: list[dict[str, Any]] = []
    for case in cases if cases is not None else load_golden_cases():
        stage = str(case["stage"])
        context = dict(case["context"])
        try:
            proposed = provider.propose(stage=stage, context=context, attempt=1)
            candidate = dict(getattr(proposed, "candidate", None) or {})
        except Exception as exc:
            results.append(
                {
                    "case_id": case.get("id"),
                    "stage": stage,
                    "passed": False,
                    "failures": [f"generation_error: {exc}"],
                }
            )
            continue
        evaluation = evaluate_candidate(case, candidate)
        evaluation["candidate"] = candidate
        results.append(evaluation)
    passed = [item for item in results if item["passed"]]
    return {
        "success": len(passed) == len(results),
        "total": len(results),
        "passed": len(passed),
        "failed": len(results) - len(passed),
        "results": results,
    }


def _numeric_values(value: Any) -> list[float]:
    values: list[float] = []
    if isinstance(value, dict):
        for nested in value.values():
            values.extend(_numeric_values(nested))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        values.append(float(value))
    return values


def _check_change_policy(
    expectations: dict[str, Any],
    candidate: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    expected_semantic = expectations.get("semantic_type")
    if expected_semantic and str(candidate.get("semantic_type")) != str(expected_semantic):
        failures.append(
            f"semantic_type={candidate.get('semantic_type')} expected={expected_semantic}"
        )
    allowed_modes = expectations.get("compare_mode_one_of")
    if allowed_modes and str(candidate.get("compare_mode")) not in {
        str(mode) for mode in allowed_modes
    }:
        failures.append(
            f"compare_mode={candidate.get('compare_mode')} not in {allowed_modes}"
        )
    threshold_rules = dict(expectations.get("thresholds") or {})
    thresholds = candidate.get("thresholds")
    thresholds = thresholds if isinstance(thresholds, dict) else {}
    if expectations.get("forbid_empty_thresholds") and not thresholds:
        failures.append("thresholds empty (mediocre candidate: never alerts)")
    min_keys = threshold_rules.get("min_required_keys")
    if min_keys is not None and len(thresholds) < int(min_keys):
        failures.append(f"thresholds key count {len(thresholds)} < {min_keys}")
    max_keys = threshold_rules.get("max_required_keys")
    if max_keys is not None and len(thresholds) > int(max_keys):
        failures.append(f"thresholds key count {len(thresholds)} > {max_keys}")
    max_allowed = threshold_rules.get("max_allowed")
    if max_allowed is not None:
        loose = [value for value in _numeric_values(thresholds) if value > float(max_allowed)]
        if loose:
            failures.append(
                f"loose threshold values {loose} exceed max_allowed={max_allowed} "
                "(would rarely alert)"
            )
    return failures


def _check_event_rules(
    expectations: dict[str, Any],
    candidate: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    rules = candidate.get("rules")
    rules = [rule for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []
    min_count = expectations.get("min_rule_count")
    if min_count is not None and len(rules) < int(min_count):
        failures.append(f"rule count {len(rules)} < {min_count}")
    severities = {str(rule.get("severity") or "").lower() for rule in rules}
    wanted = expectations.get("severities_at_least_one_of")
    if wanted and not severities.intersection({str(item).lower() for item in wanted}):
        failures.append(f"severities {sorted(severities)} lack any of {wanted}")
    forbidden_all = expectations.get("forbid_all_severity")
    if forbidden_all and rules and severities.issubset(
        {str(item).lower() for item in forbidden_all}
    ):
        failures.append(
            f"all rules are {sorted(severities)} (mediocre: effectively never actionable)"
        )
    required_fields = expectations.get("required_condition_fields") or []
    for field in required_fields:
        if not any(str(field) in (rule.get("condition") or {}) for rule in rules):
            failures.append(f"no rule condition references field: {field}")
    return failures


def _check_observation_contract(
    expectations: dict[str, Any],
    candidate: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    primary_keys = {str(item).lower() for item in (candidate.get("primary_keys") or [])}
    include_any = expectations.get("primary_keys_include_any")
    if include_any and not primary_keys.intersection(
        {str(item).lower() for item in include_any}
    ):
        failures.append(f"primary_keys {sorted(primary_keys)} lack any of {include_any}")
    required_fields = {str(item).lower() for item in (candidate.get("required_fields") or [])}
    required_any = expectations.get("required_fields_include_any")
    if required_any and not required_fields.intersection(
        {str(item).lower() for item in required_any}
    ):
        failures.append(
            f"required_fields {sorted(required_fields)} lack any of {required_any}"
        )
    return failures
