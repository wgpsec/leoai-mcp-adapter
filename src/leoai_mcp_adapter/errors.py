from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(slots=True)
class LeoAIError(Exception):
    code: str
    message: str
    retryable: bool = False
    correlation_id: str = field(default_factory=lambda: uuid4().hex)

    def __str__(self) -> str:
        retryable = str(self.retryable).lower()
        return f"{self.code}: {self.message}; retryable={retryable}; correlation_id={self.correlation_id}"
