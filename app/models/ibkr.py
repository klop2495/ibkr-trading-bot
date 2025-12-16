from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, field_validator


def _normalize_symbol(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


class IBKRConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    port: int
    client_id: int
    readonly: bool = True


class IBKRAccountValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    value: Optional[str] = None
    currency: Optional[str] = None
    account: Optional[str] = None


class IBKRAccountSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts_utc: datetime
    values: List[IBKRAccountValue]

    @field_validator("ts_utc", mode="before")
    @classmethod
    def ensure_tz(cls, v):
        if v is None:
            return datetime.now(timezone.utc)
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class IBKRPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: Optional[str] = None
    symbol: str
    sec_type: Optional[str] = None
    currency: Optional[str] = None
    exchange: Optional[str] = None
    position: float
    avg_cost: Optional[float] = None

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, v):
        normalized = _normalize_symbol(v)
        if normalized is None:
            raise ValueError("symbol is required")
        return normalized


class IBKRPositionsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts_utc: datetime
    positions: List[IBKRPosition]

    @field_validator("ts_utc", mode="before")
    @classmethod
    def ensure_tz(cls, v):
        if v is None:
            return datetime.now(timezone.utc)
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
