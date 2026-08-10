"""Rate limiting via Redis.

Counters live inside the caller's identity bundle, so checking several scopes
for one request costs a single Redis command rather than two per scope.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.identity.core.exceptions import RateLimitError
from app.identity.core.redis.bundle import CounterResult, Increment
from app.identity.core.redis.client import RedisClient, unavailable_ok
from app.identity.core.redis.keys import (
    bundle_email_key,
    bundle_ip_key,
    bundle_user_key,
    rate_limit_field,
)


@dataclass(frozen=True, slots=True)
class Rule:
    scope: str
    key: str
    limit: int
    window_seconds: int


def by_email(scope: str, email: str, *, limit: int, window_seconds: int) -> Rule:
    return Rule(scope, bundle_email_key(email.strip().lower()), limit, window_seconds)


def by_ip(scope: str, ip_address: str, *, limit: int, window_seconds: int) -> Rule:
    return Rule(scope, bundle_ip_key(ip_address), limit, window_seconds)


def by_user(scope: str, user_id: str, *, limit: int, window_seconds: int) -> Rule:
    return Rule(scope, bundle_user_key(user_id), limit, window_seconds)


class RateLimitService:
    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def check(self, scope: str, identifier: str, *, limit: int, window_seconds: int) -> None:
        """Single-scope check. Identifiers that aren't emails or IPs get their own bundle."""
        await self.check_rules(
            [Rule(scope, f"u:s:{identifier}", limit, window_seconds)]
        )

    async def check_rules(self, rules: list[Rule]) -> None:
        """Increment every rule in one command; raise on the first breach.

        Rate limiting is a protective layer over paths that are themselves safe
        (bad credentials still fail), so if Redis is down the request proceeds
        unthrottled rather than locking every user out of the site. The warning
        from `unavailable_ok` is the signal that protection is off.
        """
        if not rules:
            return

        results = await unavailable_ok(
            self._redis.bundle(
                [
                    Increment(
                        key=rule.key,
                        field=rate_limit_field(rule.scope),
                        limit=rule.limit,
                        window_seconds=rule.window_seconds,
                    )
                    for rule in rules
                ],
                now=int(time.time()),
            ),
            default=[],
            what="rate limit check",
        )

        for rule, result in zip(rules, results, strict=False):
            if isinstance(result, CounterResult) and not result.allowed:
                raise RateLimitError(
                    f"Rate limit exceeded for {rule.scope}",
                    code=f"rate_limit_{rule.scope}",
                )
