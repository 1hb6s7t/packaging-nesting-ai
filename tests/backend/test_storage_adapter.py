from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.services import storage


def use_temp_local_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(STORAGE_BACKEND="local", STORAGE_ROOT=tmp_path)
    monkeypatch.setattr(storage, "get_settings", lambda: settings)


def test_local_storage_adapter_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_temp_local_storage(monkeypatch, tmp_path)
    key = f"tests/{uuid4().hex}/artifact.txt"
    stored = storage.write_text(key, "hello storage", content_type="text/plain; charset=utf-8")

    assert stored.backend == "local"
    assert stored.object_key == key
    assert Path(stored.storage_key).resolve().relative_to(tmp_path.resolve())
    assert stored.etag
    assert stored.version_id
    assert stored.size == len("hello storage")
    assert storage.exists(stored.storage_key)
    info = storage.inspect_object(stored.storage_key, version_id=stored.version_id)
    assert info.exists is True
    assert info.etag == stored.etag
    assert info.version_id == stored.version_id
    assert info.size == stored.size
    assert storage.read_text(stored.storage_key) == "hello storage"
    assert storage.local_path(stored.storage_key) is not None
    assert storage.filename(stored.storage_key) == "artifact.txt"


def test_minio_storage_uri_parser() -> None:
    parsed = storage.parse_storage_key("minio://packaging-nesting/exports/sol_1/export.pdf")
    assert parsed["backend"] == "minio"
    assert parsed["bucket"] == "packaging-nesting"
    assert parsed["object_key"] == "exports/sol_1/export.pdf"
    assert storage.filename("minio://packaging-nesting/exports/sol_1/export.pdf") == "export.pdf"


def test_object_key_normalization_accepts_relative_keys() -> None:
    assert storage.normalize_object_key(r"exports\sol_1\artifact.pdf") == "exports/sol_1/artifact.pdf"
    assert storage.normalize_object_key("./exports//sol_1/artifact.pdf") == "exports/sol_1/artifact.pdf"


@pytest.mark.parametrize(
    "object_key",
    [
        "../outside.txt",
        "exports/../outside.txt",
        "/absolute/outside.txt",
        "C:/absolute/outside.txt",
        "minio://bucket/outside.txt",
        "",
        ".",
    ],
)
def test_object_key_normalization_rejects_unsafe_keys(object_key: str) -> None:
    with pytest.raises(ValueError):
        storage.normalize_object_key(object_key)
