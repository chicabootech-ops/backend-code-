"""Optional Cloudflare R2 client for admin avatar previews."""

from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


class R2Client:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        get_ttl_seconds: int = 3600,
    ) -> None:
        self._bucket = bucket_name
        self._get_ttl = get_ttl_seconds
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def create_presigned_get(self, key: str) -> str | None:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=self._get_ttl,
            )
        except ClientError:
            return None
