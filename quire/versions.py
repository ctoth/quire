from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import total_ordering

_PLACEHOLDER_VALUES = frozenset({"0", "0.0", "0.1", "1", "1.0"})
_CALENDAR_VERSION_RE = re.compile(r"^(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})$")


@total_ordering
@dataclass(frozen=True)
class VersionId:
    value: str

    def __init__(self, value: str, *, allow_placeholder: bool = True) -> None:
        normalized = value.strip()
        if not normalized:
            raise ValueError("VersionId cannot be empty")
        if not allow_placeholder:
            _parse_calendar_version(normalized)
            if normalized in _PLACEHOLDER_VALUES:
                raise ValueError(f"Placeholder contract version is not allowed: {normalized}")
        object.__setattr__(self, "value", normalized)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, VersionId):
            return NotImplemented
        return _parse_calendar_version(self.value) < _parse_calendar_version(other.value)

    def __str__(self) -> str:
        return self.value


def _parse_calendar_version(value: str) -> date:
    match = _CALENDAR_VERSION_RE.match(value)
    if match is None:
        raise ValueError("Contract versions must use YYYY.MM.DD")
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError as exc:
        raise ValueError("Contract versions must use YYYY.MM.DD") from exc
