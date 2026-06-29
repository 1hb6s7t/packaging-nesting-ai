from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from app.core.config import get_settings


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
    object_key: str
    backend: str
    size: int
    etag: str | None = None
    version_id: str | None = None


@dataclass(frozen=True)
class StorageObjectInfo:
    storage_key: str
    object_key: str
    backend: str
    bucket: str | None = None
    exists: bool = False
    size: int | None = None
    etag: str | None = None
    version_id: str | None = None
    last_modified: str | None = None
    error: str | None = None


def write_bytes(object_key: str, data: bytes, content_type: str = "application/octet-stream") -> StoredObject:
    normalized = normalize_object_key(object_key)
    settings = get_settings()
    if settings.storage_backend.lower() == "minio":
        client = _minio_client()
        _ensure_bucket(client)
        result = client.put_object(
            settings.minio_bucket,
            normalized,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return StoredObject(
            storage_key=f"minio://{settings.minio_bucket}/{quote(normalized)}",
            object_key=normalized,
            backend="minio",
            size=len(data),
            etag=str(getattr(result, "etag", "") or "") or None,
            version_id=str(getattr(result, "version_id", "") or "") or None,
        )

    target = settings.storage_root / normalized
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    stat = target.stat()
    return StoredObject(
        storage_key=str(target),
        object_key=normalized,
        backend="local",
        size=len(data),
        etag=hashlib.sha256(data).hexdigest(),
        version_id=_local_version_id(stat.st_mtime_ns, stat.st_size),
    )


def write_text(object_key: str, text: str, content_type: str = "text/plain; charset=utf-8") -> StoredObject:
    return write_bytes(object_key, text.encode("utf-8"), content_type=content_type)


def read_bytes(storage_key: str, version_id: str | None = None) -> bytes:
    parsed = parse_storage_key(storage_key)
    if parsed["backend"] == "minio":
        response = _minio_client().get_object(parsed["bucket"], parsed["object_key"], version_id=version_id)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
    return Path(storage_key).read_bytes()


def read_text(storage_key: str, encoding: str = "utf-8", errors: str = "ignore") -> str:
    return read_bytes(storage_key).decode(encoding, errors=errors)


def exists(storage_key: str) -> bool:
    return inspect_object(storage_key).exists


def inspect_object(storage_key: str, version_id: str | None = None) -> StorageObjectInfo:
    parsed = parse_storage_key(storage_key)
    if parsed["backend"] == "minio":
        try:
            stat = _minio_client().stat_object(parsed["bucket"], parsed["object_key"], version_id=version_id)
            return StorageObjectInfo(
                storage_key=storage_key,
                backend="minio",
                bucket=parsed["bucket"],
                object_key=parsed["object_key"],
                exists=True,
                size=getattr(stat, "size", None),
                etag=str(getattr(stat, "etag", "") or "") or None,
                version_id=str(getattr(stat, "version_id", "") or version_id or "") or None,
                last_modified=getattr(stat, "last_modified", None).isoformat()
                if getattr(stat, "last_modified", None)
                else None,
            )
        except Exception as exc:
            return StorageObjectInfo(
                storage_key=storage_key,
                backend="minio",
                bucket=parsed["bucket"],
                object_key=parsed["object_key"],
                exists=False,
                version_id=version_id,
                error=str(exc),
            )
    path = Path(storage_key)
    if not path.exists():
        return StorageObjectInfo(
            storage_key=storage_key,
            backend="local",
            object_key=storage_key,
            exists=False,
            error="file missing",
        )
    stat = path.stat()
    return StorageObjectInfo(
        storage_key=storage_key,
        backend="local",
        object_key=storage_key,
        exists=True,
        size=stat.st_size,
        etag=_file_sha256(path),
        version_id=_local_version_id(stat.st_mtime_ns, stat.st_size),
        last_modified=str(stat.st_mtime_ns),
    )


def local_path(storage_key: str) -> Path | None:
    parsed = parse_storage_key(storage_key)
    if parsed["backend"] == "minio":
        return None
    path = Path(storage_key)
    return path if path.exists() else None


def filename(storage_key: str) -> str:
    parsed = parse_storage_key(storage_key)
    if parsed["backend"] == "minio":
        return Path(parsed["object_key"]).name
    return Path(storage_key).name


def readiness_check() -> dict:
    settings = get_settings()
    backend = settings.storage_backend.lower()
    if backend == "minio":
        client = _minio_client()
        _ensure_bucket(client)
        return {
            "backend": "minio",
            "bucket": settings.minio_bucket,
            "endpoint": settings.minio_endpoint,
            "writable": True,
        }

    probe_key = f".health/{uuid4().hex}.tmp"
    probe_path = settings.storage_root / probe_key
    probe_path.parent.mkdir(parents=True, exist_ok=True)
    probe_path.write_bytes(b"ok")
    try:
        if probe_path.read_bytes() != b"ok":
            raise OSError("storage readiness probe readback mismatch")
        return {
            "backend": "local",
            "root": str(settings.storage_root),
            "writable": True,
        }
    finally:
        try:
            probe_path.unlink(missing_ok=True)
        except OSError:
            pass


def parse_storage_key(storage_key: str) -> dict[str, str]:
    parsed = urlparse(storage_key)
    if parsed.scheme == "minio":
        return {
            "backend": "minio",
            "bucket": parsed.netloc,
            "object_key": unquote(parsed.path.lstrip("/")),
        }
    return {"backend": "local", "bucket": "", "object_key": storage_key}


def normalize_object_key(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized:
        raise ValueError("object key cannot be empty")
    if normalized.startswith("/") or "://" in normalized:
        raise ValueError("object key must be a relative storage path")
    parts = []
    for part in normalized.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError("object key cannot contain traversal segments")
        if not parts and len(part) == 2 and part[1] == ":" and part[0].isalpha():
            raise ValueError("object key must be a relative storage path")
        parts.append(part)
    if not parts:
        raise ValueError("object key cannot be empty")
    return "/".join(parts)


def _minio_client():
    from minio import Minio

    settings = get_settings()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _ensure_bucket(client) -> None:
    bucket = get_settings().minio_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_version_id(mtime_ns: int, size: int) -> str:
    return f"local-{mtime_ns}-{size}"
