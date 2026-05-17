from __future__ import annotations

import operator

import pytest

from quire.versions import VersionId


_EQUALITY_OPS = (
    (operator.eq, False),
    (operator.ne, True),
)

_ORDERING_OPS = (operator.lt, operator.le, operator.gt, operator.ge)

_OTHER_VALUES = ("some-string", 42, object(), None)


@pytest.mark.parametrize("other", _OTHER_VALUES)
@pytest.mark.parametrize("op,expected", _EQUALITY_OPS)
def test_version_id_equality_falls_back_for_unsupported_types(op, expected, other):
    version = VersionId("2026.05.17")
    assert op(version, other) is expected


@pytest.mark.parametrize("other", _OTHER_VALUES)
@pytest.mark.parametrize("op", _ORDERING_OPS)
def test_version_id_ordering_raises_for_unsupported_types(op, other):
    version = VersionId("2026.05.17")
    with pytest.raises(TypeError):
        op(version, other)
