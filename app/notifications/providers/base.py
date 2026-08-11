"""The provider contract.

Business logic talks to `NotificationService`, never to a provider. Providers
know how to put one message on one wire and how to describe what happened; they
know nothing about fallback, consent, idempotency or ordering.

The one rule every implementation must honour: **classify honestly**. Returning
`PERMANENT` for something transient burns a fallback the recipient did not need;
returning `PERMANENT` for a timeout sends a duplicate. When in doubt the answer
is `UNKNOWN`, which costs a reconciliation and nothing else.
"""

from __future__ import annotations

import abc

from app.notifications.types import Channel, OutboundMessage, Provider, ProviderResult


class NotificationProvider(abc.ABC):
    """One transport for one channel."""

    #: Which provider this is, for attempt rows and logs.
    provider: Provider
    #: Which channel it serves.
    channel: Channel

    @property
    @abc.abstractmethod
    def configured(self) -> bool:
        """False when credentials are missing.

        The router skips unconfigured providers rather than attempting and
        failing, so a half-configured deployment degrades to whatever does work
        instead of erroring on every send.
        """

    @abc.abstractmethod
    async def send(self, message: OutboundMessage) -> ProviderResult:
        """Attempt delivery exactly once.

        Must not raise for ordinary provider failures — those are reported as a
        `ProviderResult` with an `error_class`. Raising is reserved for
        programming errors.
        """

    async def fetch_status(self, provider_message_id: str) -> ProviderResult | None:
        """Re-check a message whose outcome we never learned.

        Optional: providers that expose no status API return None, and the
        caller falls back to its configured unknown-policy.
        """
        return None
