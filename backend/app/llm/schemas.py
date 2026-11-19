"""Structured output schemas for the LLM (validated with Pydantic, hard rule: validate LLM output).

The LLM only extracts and classifies text. It never returns prices, totals,
stock or delivery fees (hard rule 1); those come from code/SQL.
OpenAI strict structured output needs every field present, so optional values
are `X | None` without defaults.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal["order", "question", "confirm", "cancel", "edit_order", "other"]

# Forced-escalation reasons the LLM can spot in the text (CLAUDE.md rule 5). unknown_zone and
# exact large-order limits are decided in code; not_in_data comes from the Q&A step.
SensitiveReason = Literal[
    "complaint", "discount", "large_order", "allergy_ingredients", "human_requested", "order_status",
]


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_text: str = Field(description="Product name as written, singular, without quantity/color/size")
    color: str | None = Field(description="Masculine singular with hamza, e.g. أحمر، أسود")
    size: str | None = Field(description='Uppercase Latin letters (M, L) or Latin digits ("42"), "50ml"')
    quantity: int = Field(description="Whole number, 1 if not mentioned")
    notes: str | None = Field(description="Free notes that don't change stock, e.g. gift wrap")


class MessageAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent
    sensitive_reason: SensitiveReason | None
    items: list[ExtractedItem]
    address_text: str | None
    phone: str | None
    wants_to_confirm: bool
