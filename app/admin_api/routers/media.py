"""Admin image uploads — section covers, product galleries, testimonial photos."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app.admin_api.core.exceptions import ValidationError
from app.admin_api.dependencies import CurrentAdmin
from app.admin_api.schemas.media import MediaUploadListOut, MediaUploadOut
from app.admin_api.services import media_service

router = APIRouter(prefix="/admin/media", tags=["admin-media"])

MAX_FILES_PER_REQUEST = 12


@router.get("/status")
async def storage_status(_admin: CurrentAdmin) -> dict[str, bool | int | list[str]]:
    """Lets the admin UI disable the uploader instead of failing on submit."""
    return {
        "configured": media_service.storage_configured(),
        "max_bytes": media_service.MAX_IMAGE_BYTES,
        "content_types": sorted(media_service.EXTENSION_BY_CONTENT_TYPE),
    }


@router.post("/images", response_model=MediaUploadOut, status_code=201)
async def upload_image(
    _admin: CurrentAdmin,
    file: Annotated[UploadFile, File()],
    folder: Annotated[str, Form()] = "categories",
) -> MediaUploadOut:
    return media_service.upload_image(
        data=await file.read(),
        filename=file.filename,
        folder=folder,
    )


@router.post("/images/batch", response_model=MediaUploadListOut, status_code=201)
async def upload_images(
    _admin: CurrentAdmin,
    files: Annotated[list[UploadFile], File()],
    folder: Annotated[str, Form()] = "products",
) -> MediaUploadListOut:
    if not files:
        raise ValidationError("No files were uploaded.", code="empty_upload")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise ValidationError(
            f"Upload at most {MAX_FILES_PER_REQUEST} images at a time.",
            code="too_many_files",
        )

    items = [
        media_service.upload_image(
            data=await item.read(),
            filename=item.filename,
            folder=folder,
        )
        for item in files
    ]
    return MediaUploadListOut(items=items)
