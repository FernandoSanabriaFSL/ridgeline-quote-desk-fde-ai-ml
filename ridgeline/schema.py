"""Output schema for one RFQ. Mirrors the contract that check_submission.py validates."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_serializer

Unit = Literal["EA", "BX", "CS", "PK", "PR", "RL", "FT"]
MatchMethod = Literal["ridgeline_part_number", "cross_reference", "description", "llm"]

# Strict, so bools and numeric strings are rejected instead of silently coerced.
StrictFiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Confidence = Annotated[float, Field(ge=0, le=1)]


class OutputLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: StrictInt
    as_written: str = Field(min_length=1)
    quantity: StrictInt | StrictFiniteFloat | None
    unit: Unit | None
    ridgeline_part_number: str | None
    match_method: MatchMethod | None
    confidence: Confidence | None
    needs_review: bool
    flags: list[str] = Field(default_factory=list)
    note: str | None = None

    @field_serializer("quantity")
    def _whole_numbers_as_int(self, value: int | float | None) -> int | float | None:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value


class RfqOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rfq_id: str
    customer_id: str | None
    customer_name: str | None
    lines: list[OutputLine] = Field(min_length=1)
    non_item_rows: list[str] = Field(default_factory=list)
