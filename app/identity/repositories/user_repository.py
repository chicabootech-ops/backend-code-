"""User and profile persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.identity.models import User, UserPreferences, UserProfile


def normalize_email(email: str) -> str:
    return email.strip().lower()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.profile), selectinload(User.preferences))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email_normalized(self, email_normalized: str) -> User | None:
        stmt = select(User).where(
            User.email_normalized == email_normalized,
            User.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        first_name: str | None,
        last_name: str | None,
    ) -> User:
        email_norm = normalize_email(email)
        user = User(
            id=uuid.uuid4(),
            email=email.strip(),
            email_normalized=email_norm,
            password_hash=password_hash,
            email_verified=False,
            phone_verified=False,
            status="pending_verification",
            failed_login_attempts=0,
        )
        self._session.add(user)
        await self._session.flush()

        profile = UserProfile(
            id=uuid.uuid4(),
            user_id=user.id,
            first_name=first_name,
            last_name=last_name,
        )
        preferences = UserPreferences(
            id=uuid.uuid4(),
            user_id=user.id,
        )
        self._session.add_all([profile, preferences])
        await self._session.flush()
        user.profile = profile
        user.preferences = preferences
        return user

    async def mark_email_verified(self, user: User) -> None:
        user.email_verified = True
        user.status = "active"
        user.updated_at = datetime.now()

    async def update_last_login(self, user: User) -> None:
        user.last_login_at = datetime.now()
        user.failed_login_attempts = 0
        user.locked_until = None

    async def increment_failed_login(self, user: User, *, lock_until: datetime | None) -> None:
        user.failed_login_attempts += 1
        if lock_until:
            user.locked_until = lock_until

    async def update_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        user.failed_login_attempts = 0
        user.locked_until = None

    async def update_profile(
        self,
        user: User,
        *,
        phone: str | None = None,
        profile_fields: dict | None = None,
        metadata_patch: dict | None = None,
    ) -> User:
        if phone is not None:
            user.phone = phone
            user.updated_at = datetime.now()

        if user.profile and (profile_fields or metadata_patch):
            if profile_fields:
                for key, value in profile_fields.items():
                    setattr(user.profile, key, value)
            if metadata_patch is not None:
                merged = dict(user.profile.metadata_)
                merged.update(metadata_patch)
                user.profile.metadata_ = merged
            user.profile.updated_at = datetime.now()

        await self._session.flush()
        return user
