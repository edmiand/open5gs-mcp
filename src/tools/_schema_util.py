"""Shared TypedDict pieces for structured tool output.

Every tool's top-level return is {"summary": str, "detail": <success-shape> | ErrorDetail}.
ErrorDetail is the common shape used by every early input-validation failure
across all tools — kept here once instead of redefined per tool.
"""

from typing import Literal
from typing_extensions import TypedDict


class ErrorDetail(TypedDict):
    ok: Literal[False]
    error: str
