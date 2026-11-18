"""What one agent run used: billed API cost, or tokens on the owner's Claude subscription."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Warn on the ticket when the subscription token expires within this time.
EXPIRY_WARNING = timedelta(days=14)


@dataclass
class Spend:
    cost_eur: float = 0.0  # billed (billing: api)
    tokens: int = 0  # processed on the subscription (billing: subscription)
    list_eur: float = 0.0  # what the subscription calls would cost on the API
    limits: dict[str, float] = field(default_factory=dict)  # window -> utilisation 0..1
    credential_expires_at: str | None = None  # ISO time of the subscription token

    def expires_soon(self, now: datetime) -> datetime | None:
        """The token's expiry when it is near (or past), else None."""
        if not self.credential_expires_at:
            return None
        try:
            expires = datetime.fromisoformat(self.credential_expires_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if expires.tzinfo is None or expires - now > EXPIRY_WARNING:
            return None
        return expires
