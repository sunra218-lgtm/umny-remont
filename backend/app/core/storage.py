"""Хранилище файлов. В продакшене — S3-совместимое Object Storage в РФ с приватным бакетом;
файлы отдаются только через подписанные ссылки backend (ТЗ 2.3, раздел 2)."""

import asyncio
import hashlib
import hmac
import time
import uuid
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError("bad key")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)


class S3Storage:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self._bucket_ready = False

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        existing = {b["Name"] for b in self.client.list_buckets().get("Buckets", [])}
        if self.bucket not in existing:
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_ready = True

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        self._ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._put, key, data, content_type)

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

        return await asyncio.to_thread(_get)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        _storage = S3Storage() if s.storage_backend == "s3" else LocalStorage(s.storage_local_dir)
    return _storage


def set_storage(storage: Storage) -> None:
    global _storage
    _storage = storage


def _link_sig(media_id: uuid.UUID, exp: int, viewer: uuid.UUID) -> str:
    key = get_settings().jwt_secret.encode()
    msg = f"media:{media_id}:{exp}:{viewer}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def signed_media_url(media_id: uuid.UUID, viewer: uuid.UUID) -> str:
    """Ссылка на файл, живёт 15 минут. В ссылке зашит просматривающий — для журнала и водяного
    знака при скачивании."""
    exp = int(time.time()) + get_settings().media_link_ttl_s
    sig = _link_sig(media_id, exp, viewer)
    return f"/api/v1/media/{media_id}/content?exp={exp}&v={viewer}&sig={sig}"


def check_media_link(media_id: uuid.UUID, exp: int, viewer: uuid.UUID, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(sig, _link_sig(media_id, exp, viewer))


def _file_sig(kind: str, file_id: uuid.UUID, exp: int, viewer: uuid.UUID) -> str:
    key = get_settings().jwt_secret.encode()
    msg = f"file:{kind}:{file_id}:{exp}:{viewer}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def signed_file_url(kind: str, file_id: uuid.UUID, viewer: uuid.UUID) -> str:
    """Подписанная ссылка на служебный файл (накладная, голосовая заявка), 15 минут."""
    exp = int(time.time()) + get_settings().media_link_ttl_s
    sig = _file_sig(kind, file_id, exp, viewer)
    return f"/api/v1/files/{kind}/{file_id}?exp={exp}&v={viewer}&sig={sig}"


def check_file_link(kind: str, file_id: uuid.UUID, exp: int, viewer: uuid.UUID, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(sig, _file_sig(kind, file_id, exp, viewer))
