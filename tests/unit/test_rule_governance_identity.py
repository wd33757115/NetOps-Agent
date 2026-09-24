from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.apps.rule_governance.backend.identity import actor_user_id


@pytest.mark.parametrize("value", ["", "  ", 42, True, "a" * 65])
def test_invalid_principal_cannot_be_written_as_an_id(value):
    with pytest.raises(ValueError):
        actor_user_id(value)


def test_unknown_legacy_principal_is_not_inferred():
    assert actor_user_id(None) is None
