"""Validated input contract for service-operations questions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from service_operations.metrics.query import METRIC_IDS
from service_operations.warehouse.importer import FAULT_CODES, MODELS, PRIORITIES, SITES

SHANGHAI = ZoneInfo("Asia/Shanghai")
MODES = frozenset({"auto", "metric", "document", "combined"})


def parse_instant(value: str, *, allow_date: bool = False) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("A nonempty ISO 8601 date or datetime is required")
    if allow_date and len(value) == 10:
        parsed = datetime.combine(date.fromisoformat(value), time.min, tzinfo=SHANGHAI)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Datetime must include a timezone offset")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class QueryPeriod:
    start: str
    end: str

    def bounds(self) -> tuple[datetime, datetime]:
        start = parse_instant(self.start, allow_date=True)
        end = parse_instant(self.end, allow_date=True)
        if end <= start:
            raise ValueError("period.end must be later than period.start")
        return start, end


@dataclass(frozen=True)
class ServiceOpsRequest:
    question: str
    mode: str = "auto"
    model: str | None = None
    site: str | None = None
    period: QueryPeriod | None = None
    as_of: str | None = None
    snapshot_id: str | None = None
    metric_id: str | None = None
    priority: str | None = None
    fault_code: str | None = None
    compare_previous: bool | None = None
    top_k: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("question is required")
        if len(self.question) > 2000:
            raise ValueError("question is too long")
        if self.mode not in MODES:
            raise ValueError(f"Unknown mode: {self.mode}")
        for name, allowed in (("model", MODELS), ("site", SITES),
                              ("priority", PRIORITIES), ("fault_code", FAULT_CODES)):
            value = getattr(self, name)
            if value is not None and value not in allowed:
                raise ValueError(f"Invalid {name}: {value}")
        if self.metric_id is not None and self.metric_id not in METRIC_IDS:
            raise ValueError(f"Unknown metric_id: {self.metric_id}")
        if self.period is not None:
            if not isinstance(self.period, QueryPeriod):
                raise ValueError("period must contain start and end")
            self.period.bounds()
        if self.as_of is not None:
            parse_instant(self.as_of)
        if self.snapshot_id is not None and (not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip()):
            raise ValueError("snapshot_id cannot be blank")
        if self.compare_previous is not None and not isinstance(self.compare_previous, bool):
            raise ValueError("compare_previous must be true, false, or null")
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int) or not 1 <= self.top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ServiceOpsRequest:
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown request fields: {', '.join(sorted(unknown))}")
        data = dict(value)
        period = data.get("period")
        if isinstance(period, Mapping):
            if set(period) != {"start", "end"}:
                raise ValueError("period must have exactly start and end")
            data["period"] = QueryPeriod(**period)
        return cls(**data)
