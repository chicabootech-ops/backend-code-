"""Server-side object storage helpers for generated documents (invoice PDFs).

Uploads bytes to Cloudflare R2 and mints short-lived presigned GET URLs, reusing
the same S3-compatible credentials the catalog already uses for media.
"""

from __future__ import annotations

import logging

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)


def _client():
    if not (
        settings.r2_endpoint
        and settings.effective_r2_access_key_id
        and settings.effective_r2_secret_access_key
        and settings.effective_r2_bucket_name
    ):
        return None
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        aws_access_key_id=settings.effective_r2_access_key_id,
        aws_secret_access_key=settings.effective_r2_secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def put_bytes(key: str, data: bytes, *, content_type: str = "application/octet-stream") -> bool:
    """Upload bytes to R2. Returns True on success, False if storage is unavailable."""
    client = _client()
    if client is None:
        logger.warning("R2 not configured — cannot store object %s", key)
        return False
    try:
        client.put_object(
            Bucket=settings.effective_r2_bucket_name,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return True
    except (ClientError, BotoCoreError):
        logger.exception("Failed to upload object %s to R2", key)
        return False


def presigned_get_url(key: str, *, expires: int = 3600) -> str | None:
    client = _client()
    if client is None or not key:
        return None
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.effective_r2_bucket_name, "Key": key},
            ExpiresIn=expires,
        )
    except (ClientError, BotoCoreError):
        logger.exception("Failed to presign object %s", key)
        return None


def get_bytes(key: str) -> bytes | None:
    client = _client()
    if client is None or not key:
        return None
    try:
        obj = client.get_object(Bucket=settings.effective_r2_bucket_name, Key=key)
        return obj["Body"].read()
    except (ClientError, BotoCoreError):
        logger.exception("Failed to fetch object %s", key)
        return None
