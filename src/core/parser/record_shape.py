"""Small, deterministic normalizers for TextFSM record shapes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class RecordShapeError(ValueError):
    """The emitted TextFSM values cannot represent the declared record grain."""


def expand_list_primary_records(
    records: Sequence[Mapping[str, Any]],
    primary_keys: Sequence[str],
) -> list[dict[str, Any]]:
    """Expand a TextFSM ``Value List`` primary key into one record per item.

    Some device tables print all lanes first and common thresholds afterwards.
    TextFSM must retain the lane readings as lists until the threshold rows are
    available.  When the ParserSpec explicitly makes that list field part of
    the record identity, the executable record grain is one item per list
    element.  Scalar values (for example module-wide thresholds) are copied to
    every expanded record.  Any unequal list lengths fail closed instead of
    pairing lane values by guesswork.
    """

    identity = {str(field).strip() for field in primary_keys if str(field).strip()}
    output: list[dict[str, Any]] = []
    for raw_record in records:
        record = dict(raw_record)
        list_primary_fields = [
            field
            for field in identity
            if isinstance(record.get(field), list)
        ]
        if not list_primary_fields:
            output.append(record)
            continue

        lengths = {len(record[field]) for field in list_primary_fields}
        if len(lengths) != 1 or not lengths or 0 in lengths:
            raise RecordShapeError(
                "list-valued primary keys must be non-empty and have equal lengths"
            )
        count = lengths.pop()
        list_fields = {
            field: value
            for field, value in record.items()
            if isinstance(value, list)
        }
        invalid_fields = [
            field for field, value in list_fields.items() if len(value) != count
        ]
        if invalid_fields:
            raise RecordShapeError(
                "list-valued fields do not align with the primary-key list: "
                + ", ".join(sorted(invalid_fields))
            )
        for index in range(count):
            output.append(
                {
                    field: value[index] if field in list_fields else value
                    for field, value in record.items()
                }
            )
    return output


__all__ = ["RecordShapeError", "expand_list_primary_records"]
