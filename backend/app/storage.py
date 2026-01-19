import os
import shutil
import hashlib
import uuid
import gzip
import time
import json
from pathlib import Path
from typing import Any, Optional
import ctypes
import subprocess

# path for storing the file versions
STORAGE_ROOT = Path("./.locus_storage").resolve()
CHUNK_DIR = STORAGE_ROOT / "chunks"
CHUNK_SIZE_BYTES = 4 * 1024 * 1024
CHUNKED_MIN_SIZE_BYTES = 16 * 1024 * 1024
MANIFEST_EXT = ".manifest.json"

# Default directories to exclude from tracking/snapshots.
DEFAULT_EXCLUDED_DIRS = {
    # Node / JS
    "node_modules",
    "npm-debug.log",
    "yarn.lock",
    # Python virtualenvs / caches
    "venv",
    ".venv",
    "site-packages",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    # VCS / Editor / IDE
    ".git",
    ".svn",
    ".hg",
    ".idea",
    ".vscode",
    ".ropeproject",
    # Build / packaging
    "dist",
    "build",
    "target",
    "pip-wheel-metadata",
    ".eggs",
    "egg-info",
    # OS / misc files
    ".cache",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    # Tooling / other caches
    ".sass-cache",
    ".gradle",
    "node_repl_history",
    "cache",
    # General extras
    ".classpath",
    ".project",
    ".settings",
}

CUSTOM_EXCLUDED_DIRS: set[str] = set()


def set_custom_exclusions(exclusions: list[str]) -> None:
    cleaned: set[str] = set()
    for item in exclusions:
        if not item:
            continue
        value = str(item).strip()
        if not value:
            continue
        cleaned.add(value)
    CUSTOM_EXCLUDED_DIRS.clear()
    CUSTOM_EXCLUDED_DIRS.update(cleaned)


def get_all_excluded_dirs() -> set[str]:
    return set(DEFAULT_EXCLUDED_DIRS).union(CUSTOM_EXCLUDED_DIRS)


def is_excluded_path(path: str) -> bool:
    try:
        parts = Path(os.path.normcase(path)).parts
    except Exception:
        return False
    excluded = get_all_excluded_dirs()
    for part in parts:
        if part in excluded:
            return True
    return False


def init_storage():
    """Ensure storage directory exists"""
    if not STORAGE_ROOT.exists():
        STORAGE_ROOT.mkdir(parents=True)
    if not CHUNK_DIR.exists():
        CHUNK_DIR.mkdir(parents=True)
    _protect_storage_root()


def _protect_storage_root() -> None:
    if os.name != "nt":
        return

    FILE_ATTRIBUTE_HIDDEN = 0x02
    FILE_ATTRIBUTE_SYSTEM = 0x04

    try:
        attrs = FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
        ctypes.windll.kernel32.SetFileAttributesW(str(STORAGE_ROOT), attrs)
    except Exception:
        pass


def is_admin_user() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_icacls(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "icacls failed").strip()
        return True, (result.stdout or "ok").strip()
    except Exception as e:
        return False, str(e)


def enable_admin_protection() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Admin protection is only supported on Windows"
    if not is_admin_user():
        return False, "Administrator privileges are required"

    # Remove inheritance and deny Users read/execute. Allow Administrators full control.
    ok, msg = _run_icacls(
        [
            "icacls",
            str(STORAGE_ROOT),
            "/inheritance:r",
            "/grant:r",
            "Administrators:(OI)(CI)F",
            "/deny",
            "Users:(OI)(CI)RX",
        ]
    )
    return ok, msg


def disable_admin_protection() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Admin protection is only supported on Windows"
    if not is_admin_user():
        return False, "Administrator privileges are required"

    ok, msg = _run_icacls(
        [
            "icacls",
            str(STORAGE_ROOT),
            "/remove:d",
            "Users",
        ]
    )
    if not ok:
        return ok, msg

    # Restore inheritance
    return _run_icacls(["icacls", str(STORAGE_ROOT), "/inheritance:e"])


def storage_subdir_name(watched_path: str) -> str:
    base = os.path.basename(os.path.normpath(watched_path)) or "watched"
    short_hash = hashlib.sha256(watched_path.encode("utf-8")).hexdigest()[:6]
    safe_base = "".join(c for c in base if c.isalnum() or c in ("-", "_"))
    safe_base = safe_base or "watched"
    return f"{safe_base}-{short_hash}"


def ensure_snapshot_dir(subdir_name: str) -> Path:
    target = STORAGE_ROOT / subdir_name
    target.mkdir(parents=True, exist_ok=True)
    return target


def mirror_copy_file(src_path: str, watched_root: str, subdir_name: str) -> Path:
    rel = os.path.relpath(src_path, watched_root)
    dest_path = STORAGE_ROOT / subdir_name / rel
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest_path)
    return dest_path


def calculate_file_hash(file_path: str) -> Optional[str]:
    """Calculate SHA256 hash of a file"""
    if not os.path.exists(file_path):
        return None

    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Read and update hash string value in blocks of 4K
        # CHANGED: standard efficient reading of large files
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def _write_compressed(src_path: str, dest_path: Path) -> None:
    with open(src_path, "rb") as f_in:
        with gzip.open(dest_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)


def _stream_hash_and_compress(src_path: str, temp_path: Path) -> str:
    sha256_hash = hashlib.sha256()
    with open(src_path, "rb") as f_in:
        with gzip.open(temp_path, "wb") as f_out:
            for chunk in iter(lambda: f_in.read(4096), b""):
                sha256_hash.update(chunk)
                f_out.write(chunk)
    return sha256_hash.hexdigest()


def _chunk_path(chunk_hash: str) -> Path:
    return CHUNK_DIR / f"{chunk_hash}.chunk"


def _save_chunked_file(
    src_path: str, file_size: int, known_hash: Optional[str] = None
) -> Optional[dict[str, Any]]:
    if known_hash:
        manifest_name = f"{known_hash}{MANIFEST_EXT}"
        manifest_path = STORAGE_ROOT / manifest_name
        if manifest_path.exists():
            return {
                "storage_path": str(manifest_path),
                "file_hash": known_hash,
                "file_size": file_size,
                "filename": manifest_name,
            }

    file_hasher = hashlib.sha256()
    chunks: list[dict[str, Any]] = []

    try:
        with open(src_path, "rb") as f_in:
            for chunk in iter(lambda: f_in.read(CHUNK_SIZE_BYTES), b""):
                file_hasher.update(chunk)
                chunk_hash = hashlib.sha256(chunk).hexdigest()
                chunk_path = _chunk_path(chunk_hash)
                if not chunk_path.exists():
                    with open(chunk_path, "wb") as f_out:
                        f_out.write(chunk)
                chunks.append({"hash": chunk_hash, "size": len(chunk)})

        file_hash = file_hasher.hexdigest()
        manifest_name = f"{file_hash}{MANIFEST_EXT}"
        manifest_path = STORAGE_ROOT / manifest_name

        if not manifest_path.exists():
            manifest = {
                "file_hash": file_hash,
                "file_size": file_size,
                "chunk_size": CHUNK_SIZE_BYTES,
                "chunks": chunks,
            }
            with open(manifest_path, "w", encoding="utf-8") as f_manifest:
                json.dump(manifest, f_manifest)

        return {
            "storage_path": str(manifest_path),
            "file_hash": file_hash,
            "file_size": file_size,
            "filename": manifest_name,
        }
    except Exception as e:
        print(f"[Storage] Failed chunked backup for {src_path}: {e}")
        return None


def _save_with_known_hash(
    src_path: str, file_hash: str, file_size: int
) -> Optional[dict[str, Any]]:
    storage_filename = f"{file_hash}.gz"
    storage_path = STORAGE_ROOT / storage_filename

    if storage_path.exists():
        print(
            f"[Storage] Deduplication hit! Content {file_hash[:8]}... already exists."
        )
        return {
            "storage_path": str(storage_path),
            "file_hash": file_hash,
            "file_size": file_size,
            "filename": storage_filename,
        }

    try:
        _write_compressed(src_path, storage_path)
        print(f"[Storage] Saved new content: {storage_filename}")
        return {
            "storage_path": str(storage_path),
            "file_hash": file_hash,
            "file_size": file_size,
            "filename": storage_filename,
        }
    except Exception as e:
        print(f"[Storage] Failed to backup file {src_path}: {e}")
        if storage_path.exists():
            try:
                os.remove(storage_path)
            except OSError:
                pass
        return None


def _save_with_unknown_hash(src_path: str, file_size: int) -> Optional[dict[str, Any]]:
    temp_name = f".{uuid.uuid4().hex}.gz.tmp"
    temp_path = STORAGE_ROOT / temp_name

    try:
        file_hash = _stream_hash_and_compress(src_path, temp_path)
        storage_filename = f"{file_hash}.gz"
        storage_path = STORAGE_ROOT / storage_filename

        if storage_path.exists():
            print(
                f"[Storage] Deduplication hit! Content {file_hash[:8]}... already exists."
            )
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return {
                "storage_path": str(storage_path),
                "file_hash": file_hash,
                "file_size": file_size,
                "filename": storage_filename,
            }

        os.replace(temp_path, storage_path)
        print(f"[Storage] Saved new content: {storage_filename}")
        return {
            "storage_path": str(storage_path),
            "file_hash": file_hash,
            "file_size": file_size,
            "filename": storage_filename,
        }
    except Exception as e:
        print(f"[Storage] Failed to backup file {src_path}: {e}")
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return None


def save_file_version(
    src_path: str, known_hash: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """
    Copies the file to the storage directory using Content-Addressed Storage.

    Content Addressed Storage:
    1. Calculate the unique fingerprint (SHA-256 hash) of the file content.
    2. Check if we already have a file with that fingerprint in our storage.
    3. If YES (Deduplication): We skip writing the file again! We just return the path to the existing one.
       This saves massive space for duplicate files (e.g., File A and Copy of File A).
    4. If NO (New Content): We compress the file (GZIP) to save more space and write it.
    """
    init_storage()

    if not os.path.exists(src_path):
        return None

    file_size = os.path.getsize(src_path)

    if file_size >= CHUNKED_MIN_SIZE_BYTES:
        return _save_chunked_file(src_path, file_size, known_hash=known_hash)

    if known_hash:
        return _save_with_known_hash(src_path, known_hash, file_size)

    return _save_with_unknown_hash(src_path, file_size)


def restore_file_version(storage_path: str, dest_path: str) -> bool:
    """
    Restores a version from storage to destination.
    Handles both new compressed (.gz) files and old legacy files.
    """
    if not os.path.exists(storage_path):
        return False

    try:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        if str(storage_path).endswith(MANIFEST_EXT):
            with open(storage_path, "r", encoding="utf-8") as f_manifest:
                manifest = json.load(f_manifest)
            with open(dest_path, "wb") as f_out:
                for chunk in manifest.get("chunks", []):
                    chunk_path = _chunk_path(chunk["hash"])
                    if not chunk_path.exists():
                        return False
                    with open(chunk_path, "rb") as f_in:
                        shutil.copyfileobj(f_in, f_out)
        # Check if the stored file is one of our new compressed ones
        elif str(storage_path).endswith(".gz"):
            # COMMENT: It's a compressed file! Decompress it back to the destination.
            with gzip.open(storage_path, "rb") as f_in:
                with open(dest_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
        else:
            # COMMENT: It's an old legacy file (not compressed). Just copy it directly.
            shutil.copy2(storage_path, dest_path)

        return True
    except Exception as e:
        print(f"[Storage] Failed to restore file: {e}")
        return False


def get_total_storage_usage():
    """Calculates total size of the .locus_storage directory in bytes."""
    total_size = 0
    if STORAGE_ROOT.exists():
        for f in STORAGE_ROOT.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
    return total_size


GC_GRACE_PERIOD_SECONDS = 60 * 60  # 60 minutes


def _is_within_grace_period(stored_file: Path, now: float) -> bool:
    try:
        return now - stored_file.stat().st_mtime < GC_GRACE_PERIOD_SECONDS
    except OSError as e:
        print(f"[Storage] GC: Skipping {stored_file.name} (stat failed: {e})")
        return True


def run_garbage_collection(active_file_hashes: set):
    """
    Simple Garbage Collection (GC).

    1. Look at all files in our storage folder.
    2. Check if their Hash is in the 'active_file_hashes' list (from the Database).
    3. If a file is NOT in the list, it means no version uses this content anymore.
    4. Skip files created/modified recently (grace period).
    5. Delete the unused file to free up space.
    """
    print("[Storage] Starting Garbage Collection...")
    cleaned_count = 0
    cleaned_bytes = 0

    if not STORAGE_ROOT.exists():
        return

    now = time.time()

    for stored_file in STORAGE_ROOT.iterdir():
        if not stored_file.is_file():
            continue

        # Grace period: do not delete files that are too new
        if _is_within_grace_period(stored_file, now):
            continue

        # Get the hash from filename (remove .gz if present)
        # Logic: Filename is "{hash}.gz" or "{hash}" (legacy uuid isn't a hash but logic holds)
        # For our CAS files, filename IS the header.

        # NOTE: Legacy files (UUIDs) won't match a hash set, so they might be deleted
        # if we aren't careful. For now, we strictly only GC files that look like our hashes
        # or we assume 'active_file_hashes' includes filenames too.
        # To be safe: We will just check if the full filename matches any metadata record.

        # Simplified approach: We assume active_file_hashes contains the *storage filenames* not just hashes.
        if stored_file.name not in active_file_hashes:
            try:
                size = stored_file.stat().st_size
                stored_file.unlink()  # Delete the file
                cleaned_count += 1
                cleaned_bytes += size
                print(f"[Storage] GC: Deleted unreferenced file {stored_file.name}")
            except Exception as e:
                print(f"[Storage] GC: Failed to delete {stored_file.name}: {e}")

    print(
        f"[Storage] GC Complete. Removed {cleaned_count} files, freed {cleaned_bytes / 1024 / 1024:.2f} MB."
    )
