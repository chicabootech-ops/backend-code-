"""Device, login history, security log, consent persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentRecord, LoginHistory, SecurityLog, UserDevice


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_device(
        self,
        *,
        user_id: uuid.UUID,
        ip_address: str | None,
        user_agent: str | None,
        device_name: str | None,
        device_type: str,
    ) -> UserDevice:
        stmt = (
            select(UserDevice)
            .where(
                UserDevice.user_id == user_id,
                UserDevice.user_agent == user_agent,
                UserDevice.revoked_at.is_(None),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        device = result.scalar_one_or_none()
        now = datetime.now().astimezone()
        if device:
            device.last_seen_at = now
            device.ip_address = ip_address
            device.device_name = device_name or device.device_name
            device.device_type = device_type
            return device

        device = UserDevice(
            id=uuid.uuid4(),
            user_id=user_id,
            device_name=device_name,
            device_type=device_type,
            ip_address=ip_address,
            user_agent=user_agent,
            last_seen_at=now,
        )
        self._session.add(device)
        await self._session.flush()
        return device

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserDevice]:
        stmt = (
            select(UserDevice)
            .where(UserDevice.user_id == user_id, UserDevice.revoked_at.is_(None))
            .order_by(UserDevice.last_seen_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_user(self, user_id: uuid.UUID, device_id: uuid.UUID) -> UserDevice | None:
        stmt = select(UserDevice).where(
            UserDevice.id == device_id,
            UserDevice.user_id == user_id,
            UserDevice.revoked_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke(self, device: UserDevice) -> None:
        device.revoked_at = datetime.now().astimezone()
        await self._session.flush()


class LoginHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID | None,
        email_attempted: str | None,
        success: bool,
        failure_reason: str | None,
        ip_address: str | None,
        user_agent: str | None,
        device_id: uuid.UUID | None,
        request_id: str | None,
    ) -> LoginHistory:
        row = LoginHistory(
            id=uuid.uuid4(),
            user_id=user_id,
            email_attempted=email_attempted,
            success=success,
            failure_reason=failure_reason,
            ip_address=ip_address,
            user_agent=user_agent,
            device_id=device_id,
            request_id=request_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LoginHistory], int]:
        count_stmt = select(func.count()).select_from(LoginHistory).where(LoginHistory.user_id == user_id)
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one())

        stmt = (
            select(LoginHistory)
            .where(LoginHistory.user_id == user_id)
            .order_by(LoginHistory.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total


class SecurityLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID | None,
        event_type: str,
        ip_address: str | None,
        user_agent: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> SecurityLog:
        row = SecurityLog(
            id=uuid.uuid4(),
            user_id=user_id,
            event_type=event_type,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_=metadata or {},
        )
        self._session.add(row)
        await self._session.flush()
        return row


class ConsentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID,
        consent_type: str,
        granted: bool,
        source: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> ConsentRecord:
        row = ConsentRecord(
            id=uuid.uuid4(),
            user_id=user_id,
            consent_type=consent_type,
            granted=granted,
            source=source,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._session.add(row)
        await self._session.flush()
        return row
