from src.observability.read_metrics import (
    _count_query,
    latest_read_observation,
    observe_read,
)


def test_observe_read_records_query_count_result_size_and_variant():
    @observe_read(
        "test_read",
        variant=lambda args: str(args["variant"]),
        result_count=lambda result: len(result["items"]),
    )
    def read(variant="default"):
        _count_query()
        return {"items": [1, 2]}

    assert read("with_projection") == {"items": [1, 2]}
    observation = latest_read_observation("test_read", "with_projection")

    assert observation is not None
    assert observation["query_count"] == 1
    assert observation["result_count"] == 2
    assert observation["duration_seconds"] >= 0
