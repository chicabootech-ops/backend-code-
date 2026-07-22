from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.identity.dependencies import AccountServiceDep, CurrentUserId, DbSession
from app.identity.schemas.address import (
    AddressCreateRequest,
    AddressListResponse,
    AddressResponse,
    AddressUpdateRequest,
)
from app.identity.schemas.common import MessageResponse

router = APIRouter(prefix="/api/user/me/addresses", tags=["addresses"])


@router.get("", response_model=AddressListResponse)
async def list_addresses(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
) -> AddressListResponse:
    return await account.list_addresses(session, user_id)


@router.post("", response_model=AddressResponse, status_code=201)
async def create_address(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    body: AddressCreateRequest,
) -> AddressResponse:
    return await account.create_address(session, user_id, body)


@router.get("/{address_id}", response_model=AddressResponse)
async def get_address(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    address_id: uuid.UUID,
) -> AddressResponse:
    return await account.get_address(session, user_id, address_id)


@router.patch("/{address_id}", response_model=AddressResponse)
async def update_address(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    address_id: uuid.UUID,
    body: AddressUpdateRequest,
) -> AddressResponse:
    return await account.update_address(session, user_id, address_id, body)


@router.delete("/{address_id}", response_model=MessageResponse)
async def delete_address(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    address_id: uuid.UUID,
) -> MessageResponse:
    return await account.delete_address(session, user_id, address_id)


@router.post("/{address_id}/default", response_model=AddressResponse)
async def set_default_address(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    address_id: uuid.UUID,
) -> AddressResponse:
    return await account.set_default_address(session, user_id, address_id)
