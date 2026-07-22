"""Cloudflare R2 (S3-compatible) client."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.identity.core.exceptions import AppError, NotFoundError, ValidationError


class R2Client:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        upload_ttl_seconds: int = 900,
        get_ttl_seconds: int = 3600,
    ) -> None:
        self._bucket = bucket_name
        self._upload_ttl = upload_ttl_seconds
        self._get_ttl = get_ttl_seconds
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def avatar_key(self, user_id: uuid.UUID) -> str:
        return f"avatars/{user_id}.webp"

    def create_presigned_put(
        self,
        *,
        key: str,
        content_type: str,
        content_length: int,
    ) -> tuple[str, datetime]:
        try:
            url = self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                    "ContentLength": content_length,
                },
                ExpiresIn=self._upload_ttl,
            )
        except ClientError as exc:
            raise AppError("Failed to generate upload URL", code="r2_error", status_code=500) from exc
        expires_at = datetime.now(UTC) + timedelta(seconds=self._upload_ttl)
        return url, expires_at

    def create_presigned_get(self, key: str) -> tuple[str, datetime]:
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=self._get_ttl,
            )
        except ClientError as exc:
            raise AppError("Failed to generate avatar URL", code="r2_error", status_code=500) from exc
        expires_at = datetime.now(UTC) + timedelta(seconds=self._get_ttl)
        return url, expires_at

    def object_exists(self, key: str) -> dict[str, Any] | None:
        try:
            return self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise AppError("Failed to verify avatar upload", code="r2_error", status_code=500) from exc

    def delete_object(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return
            raise AppError("Failed to delete avatar", code="r2_error", status_code=500) from exc

    def validate_uploaded_object(self, key: str, *, max_size: int) -> None:
        meta = self.object_exists(key)
        if not meta:
            raise NotFoundError("Avatar file not found in storage", code="avatar_not_uploaded")
        size = int(meta.get("ContentLength", 0))
        if size <= 0 or size > max_size:
            raise ValidationError("Uploaded avatar exceeds allowed size", code="avatar_too_large")
