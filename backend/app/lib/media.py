from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings

LEGACY_PLACEHOLDER_IMAGE = "/collections/premium-blooms.jpg"
DEFAULT_PRODUCT_IMAGE = "/collections/tulips.jpeg"


def resolve_storage_url(image_r2_key: str | None) -> str | None:
    if not image_r2_key:
        return None
    key = image_r2_key.strip()
    if not key:
        return None
    if key == LEGACY_PLACEHOLDER_IMAGE:
        return None
    if key.startswith("/") or key.startswith("http://") or key.startswith("https://"):
        return key
    if settings.r2_public_base_url:
        return f"{settings.r2_public_base_url.rstrip('/')}/{key.lstrip('/')}"
    if not (
        settings.r2_endpoint_url
        and settings.r2_access_key
        and settings.r2_secret_key
        and settings.r2_bucket
    ):
        return None
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key,
            aws_secret_access_key=settings.r2_secret_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket, "Key": key},
            ExpiresIn=3600,
        )
    except ClientError:
        return None


def product_image_url(metadata: dict | None) -> str | None:
    meta = metadata or {}
    raw = meta.get("image_url") or meta.get("image_r2_key")
    if isinstance(raw, str):
        return resolve_storage_url(raw)
    return None
