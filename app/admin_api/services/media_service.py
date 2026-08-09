"""Admin image uploads to R2.

Covers section/category covers, product galleries and testimonial avatars. The
object key is what gets stored on the row (`image_r2_key`, `metadata.gallery`);
`resolve_storage_url` turns it back into a browsable URL at read time.
"""

from __future__ import annotations

import logging
import uuid

from app.admin_api.core.exceptions import AppError, ValidationError
from app.admin_api.schemas.media import MediaUploadOut
from app.config import settings
from app.storefront.lib.media import resolve_storage_url
from app.storefront.lib.r2_storage import put_bytes

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Content type -> extension. Anything not listed is rejected outright.
EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/gif": "gif",
}

# First bytes of each accepted format, so a renamed .exe cannot slip through.
_MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

ALLOWED_FOLDERS = frozenset({"categories", "products", "testimonials", "banners"})


def _sniff_content_type(data: bytes) -> str:
    """Identify the format from its header bytes; "" when nothing matches."""
    for prefix, content_type in _MAGIC_PREFIXES:
        if data.startswith(prefix):
            return content_type
    # RIFF....WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # ISO-BMFF branded AVIF
    if data[4:8] == b"ftyp" and b"avif" in data[8:24]:
        return "image/avif"
    return ""


def _dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Best-effort image dimensions; never fails the upload."""
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except Exception:  # noqa: BLE001 - Pillow is optional at runtime
        return None, None


def upload_image(
    *,
    data: bytes,
    filename: str | None,
    folder: str = "categories",
) -> MediaUploadOut:
    if folder not in ALLOWED_FOLDERS:
        raise ValidationError(
            f"Unknown upload folder '{folder}'.", code="invalid_upload_folder"
        )
    if not data:
        raise ValidationError("Uploaded file is empty.", code="empty_upload")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"Image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)}MB.",
            code="upload_too_large",
        )

    # The sniffed type is authoritative: a browser (or a script) can declare any
    # Content-Type it likes, so an unrecognised header signature is a rejection,
    # never a fall-back to what the client claimed.
    content_type = _sniff_content_type(data)
    if content_type not in EXTENSION_BY_CONTENT_TYPE:
        raise ValidationError(
            "Only JPEG, PNG, WebP, AVIF or GIF images can be uploaded.",
            code="unsupported_image_type",
        )

    extension = EXTENSION_BY_CONTENT_TYPE[content_type]
    key = f"{folder}/{uuid.uuid4().hex}.{extension}"

    if not put_bytes(key, data, content_type=content_type):
        raise AppError(
            "Image storage is not available. Check the R2 settings.",
            code="storage_unavailable",
            status_code=503,
        )

    width, height = _dimensions(data)
    logger.info("Uploaded %s (%s bytes) as %s", filename or "image", len(data), key)

    return MediaUploadOut(
        key=key,
        url=resolve_storage_url(key),
        content_type=content_type,
        byte_size=len(data),
        width=width,
        height=height,
    )


def storage_configured() -> bool:
    return settings.r2_configured
