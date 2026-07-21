"""auto_build_context 质量门槛与 golden 回归评估单测。"""

from __future__ import annotations

from src.core.rule_control.dataset_context import assess_context_quality
from src.core.rule_control.golden_regression import (
    evaluate_candidate,
    load_golden_cases,
    run_golden_regression,
)


def _records(count: int, *, fill: float = 1.0) -> list[dict]:
    filled = int(count * fill)
    rows = [{"crc_errors": index + 1} for index in range(filled)]
    rows.extend({"crc_errors": None} for _ in range(count - filled))
    return rows


class TestContextQualityGate:
    def test_rejects_insufficient_records(self) -> None:
        result = assess_context_quality(
            _records(2), "crc_errors", min_records=10, min_field_fill_rate=0.6
        )
        assert result["passed"] is False
        assert any("insufficient_records" in reason for reason in result["reasons"])

    def test_rejects_low_field_fill_rate(self) -> None:
        result = assess_context_quality(
            _records(20, fill=0.3), "crc_errors", min_records=10, min_field_fill_rate=0.6
        )
        assert result["passed"] is False
        assert any("low_field_fill_rate" in reason for reason in result["reasons"])

    def test_rejects_missing_target_field(self) -> None:
        result = assess_context_quality(
            _records(20), "", min_records=10, min_field_fill_rate=0.6
        )
        assert result["passed"] is False
        assert "missing_target_field" in result["reasons"]

    def test_passes_sufficient_context(self) -> None:
        result = assess_context_quality(
            _records(20, fill=0.9), "crc_errors", min_records=10, min_field_fill_rate=0.6
        )
        assert result["passed"] is True
        assert result["record_count"] == 20
        assert result["field_fill_rate"] >= 0.6


class _StubProvider:
    """按 stage 返回固定候选，用于验证 golden 评估逻辑本身。"""

    name = "stub"

    def __init__(self, candidates: dict[str, dict]) -> None:
        self._candidates = candidates

    def propose(self, *, stage: str, context: dict, attempt: int):
        class _Result:
            def __init__(self, candidate: dict) -> None:
                self.candidate = candidate

        return _Result(dict(self._candidates.get(stage) or {}))


class TestGoldenRegression:
    def test_golden_cases_load_and_are_well_formed(self) -> None:
        cases = load_golden_cases()
        assert len(cases) >= 3
        stages = {case["stage"] for case in cases}
        assert {"change_policy", "event_rules", "observation_contract"} <= stages

    def test_loose_thresholds_fail_change_policy_case(self) -> None:
        case = next(
            case for case in load_golden_cases() if case["id"] == "huawei-interface-crc-gauge"
        )
        mediocre = {
            "semantic_type": "counter",
            "compare_mode": "delta",
            # 阈值远超历史 P99，永不告警的"平庸"产物
            "thresholds": {"critical": 1000000},
        }
        result = evaluate_candidate(case, mediocre)
        assert result["passed"] is False
        assert any("loose threshold" in failure for failure in result["failures"])

    def test_empty_thresholds_fail_change_policy_case(self) -> None:
        case = next(
            case for case in load_golden_cases() if case["id"] == "huawei-interface-crc-gauge"
        )
        result = evaluate_candidate(
            case, {"semantic_type": "counter", "compare_mode": "delta", "thresholds": {}}
        )
        assert result["passed"] is False

    def test_reasonable_candidate_passes(self) -> None:
        case = next(
            case for case in load_golden_cases() if case["id"] == "huawei-interface-crc-gauge"
        )
        result = evaluate_candidate(
            case,
            {"semantic_type": "counter", "compare_mode": "delta", "thresholds": {"warning": 5}},
        )
        assert result["passed"] is True

    def test_info_only_event_rules_fail(self) -> None:
        case = next(
            case for case in load_golden_cases() if case["id"] == "cisco-version-event-rules"
        )
        result = evaluate_candidate(
            case,
            {
                "rules": [
                    {
                        "name": "version-changed",
                        "event_type": "version_change",
                        "severity": "info",
                        "condition": {"observation_type": "software_version"},
                    }
                ]
            },
        )
        assert result["passed"] is False

    def test_run_golden_regression_with_stub_provider(self) -> None:
        cases = [
            case
            for case in load_golden_cases()
            if case["id"] in {"huawei-interface-crc-gauge", "cisco-version-event-rules"}
        ]
        provider = _StubProvider(
            {
                "change_policy": {
                    "semantic_type": "counter",
                    "compare_mode": "delta",
                    "thresholds": {"warning": 5},
                },
                "event_rules": {
                    "rules": [
                        {
                            "name": "version-changed",
                            "event_type": "version_change",
                            "severity": "warning",
                            "condition": {"observation_type": "software_version"},
                        }
                    ]
                },
            }
        )
        report = run_golden_regression(provider=provider, cases=cases)
        assert report["success"] is True
        assert report["passed"] == 2
