import json
from pathlib import Path

from app import storage


def _setup_temp_storage(
    monkeypatch,
    tmp_path: Path,
    chunk_size: int = 8,
    chunked_min_size: int = 32,
):
    storage_root = tmp_path / "storage"
    chunk_dir = storage_root / "chunks"
    monkeypatch.setattr(storage, "STORAGE_ROOT", storage_root)
    monkeypatch.setattr(storage, "CHUNK_DIR", chunk_dir)
    monkeypatch.setattr(storage, "CHUNK_SIZE_BYTES", chunk_size)
    monkeypatch.setattr(storage, "CHUNKED_MIN_SIZE_BYTES", chunked_min_size)
    storage.init_storage()


def test_save_with_known_hash_dedupe_and_restore(monkeypatch, tmp_path: Path):
    _setup_temp_storage(monkeypatch, tmp_path, chunked_min_size=1024)

    src = tmp_path / "sample.txt"
    src.write_bytes(b"hello world")

    known_hash = storage.calculate_file_hash(str(src))
    meta1 = storage.save_file_version(str(src), known_hash=known_hash)
    assert meta1 is not None
    assert meta1["file_hash"] == known_hash
    assert meta1["storage_path"].endswith(".gz")

    meta2 = storage.save_file_version(str(src), known_hash=known_hash)
    assert meta2 is not None
    assert meta2["storage_path"] == meta1["storage_path"]

    restored = tmp_path / "restored.txt"
    ok = storage.restore_file_version(meta1["storage_path"], str(restored))
    assert ok is True
    assert restored.read_bytes() == src.read_bytes()


def test_save_with_unknown_hash_stream_and_restore(monkeypatch, tmp_path: Path):
    _setup_temp_storage(monkeypatch, tmp_path, chunked_min_size=1024)

    src = tmp_path / "another.txt"
    src.write_bytes(b"abc" * 100)

    meta = storage.save_file_version(str(src))
    assert meta is not None

    calculated = storage.calculate_file_hash(str(src))
    assert meta["file_hash"] == calculated
    assert meta["storage_path"].endswith(".gz")

    restored = tmp_path / "restored_unknown.txt"
    ok = storage.restore_file_version(meta["storage_path"], str(restored))
    assert ok is True
    assert restored.read_bytes() == src.read_bytes()


def test_chunked_save_and_restore(monkeypatch, tmp_path: Path):
    _setup_temp_storage(monkeypatch, tmp_path, chunked_min_size=32)

    src = tmp_path / "big.bin"
    src.write_bytes(b"abcd" * 40)  # 160 bytes; above chunked threshold

    meta = storage.save_file_version(str(src))
    assert meta is not None
    assert meta["storage_path"].endswith(storage.MANIFEST_EXT)

    manifest_path = Path(meta["storage_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["file_hash"] == meta["file_hash"]
    assert len(manifest["chunks"]) > 0

    for chunk in manifest["chunks"]:
        chunk_path = storage._chunk_path(chunk["hash"])
        assert chunk_path.exists()

    restored = tmp_path / "restored_big.bin"
    ok = storage.restore_file_version(meta["storage_path"], str(restored))
    assert ok is True
    assert restored.read_bytes() == src.read_bytes()


def test_restore_fails_when_chunk_missing(monkeypatch, tmp_path: Path):
    _setup_temp_storage(monkeypatch, tmp_path, chunked_min_size=32)

    src = tmp_path / "big_missing.bin"
    src.write_bytes(b"xyz" * 50)

    meta = storage.save_file_version(str(src))
    assert meta is not None
    assert meta["storage_path"].endswith(storage.MANIFEST_EXT)

    manifest_path = Path(meta["storage_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_chunk = manifest["chunks"][0]["hash"]
    chunk_path = storage._chunk_path(first_chunk)
    chunk_path.unlink()

    restored = tmp_path / "restored_missing.bin"
    ok = storage.restore_file_version(meta["storage_path"], str(restored))
    assert ok is False
