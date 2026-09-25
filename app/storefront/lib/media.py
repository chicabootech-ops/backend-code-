from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlsplit

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings

LEGACY_PLACEHOLDER_IMAGE = "/collections/premium-blooms.jpg"
DEFAULT_PRODUCT_IMAGE = "/collections/tulips.jpeg"


def normalize_storage_key(value: str | None) -> str | None:
    if not value:
        return value
    key = value.strip()
    url = urlsplit(key)
    if url.scheme.lower() not in {"http", "https"} and not url.netloc:
        return key
    endpoint = urlsplit(settings.r2_endpoint)
    bucket = settings.effective_r2_bucket_name
    public = urlsplit(settings.r2_public_base_url)
    path = None
    if endpoint.netloc and url.netloc.lower() == endpoint.netloc.lower():
        prefix = f"{endpoint.path.rstrip('/')}/{bucket}/"
        if url.path.startswith(prefix):
            path = url.path[len(prefix):]
    elif endpoint.hostname and url.hostname == f"{bucket}.{endpoint.hostname}":
        path = url.path[1:]
    elif public.netloc and url.netloc.lower() == public.netloc.lower():
        prefix = public.path.rstrip("/") + "/"
        if url.path.startswith(prefix):
            path = url.path[len(prefix):]
    known_host = bool(url.hostname) and url.hostname in {
        endpoint.hostname, public.hostname, f"{bucket}.{endpoint.hostname}"
    }
    if path and not url.fragment and not url.username and not url.password:
        if re.search(r"%(?![0-9a-fA-F]{2})", path):
            raise ValueError("Image URL contains invalid percent encoding")
        decoded = unquote(path, errors="strict")
        if not decoded.startswith("/") and "\x00" not in decoded:
            return decoded
    signed = any(name.lower().startswith("x-amz-") for name, _ in parse_qsl(url.query))
    r2_host = (url.hostname or "").endswith((".r2.cloudflarestorage.com", ".r2.dev"))
    if signed or r2_host or known_host:
        raise ValueError("Image URL cannot be mapped to the configured R2 bucket; supply its object key")
    return key


def normalize_image_metadata(metadata: dict) -> dict:
    result = dict(metadata)
    for field in ("image_url", "image_r2_key"):
        if isinstance(result.get(field), str):
            result[field] = normalize_storage_key(result[field])
    for field in ("gallery", "images"):
        if isinstance(result.get(field), list):
            result[field] = [
                normalize_storage_key(value) if isinstance(value, str) else value
                for value in result[field]
            ]
    return result


def resolve_storage_url(image_r2_key: str | None) -> str | None:
    if not image_r2_key:
        return None
    key = normalize_storage_key(image_r2_key)
    if not key:
        return None
    if key == LEGACY_PLACEHOLDER_IMAGE:
        return None
    if key.startswith("/") or key.startswith("http://") or key.startswith("https://"):
        return key
    if not settings.r2_configured:
        return None
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint,
            aws_access_key_id=settings.effective_r2_access_key_id,
            aws_secret_access_key=settings.effective_r2_secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.effective_r2_bucket_name, "Key": key},
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
