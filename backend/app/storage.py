import os
import shutil
import hashlib
import uuid
import gzip  # CHANGED: Imported gzip for compression
from pathlib import Path

# Define where we store the file versions
# For now, let's put it in a .locus_storage folder in the user's home directory
# or relative to the backend. Let's make it relative to the backend execution for portability in dev.
STORAGE_ROOT = Path("./.locus_storage").resolve()


def init_storage():
    """Ensure storage directory exists"""
    # CHANGED: Added comment to explain we create the folder if missing
    if not STORAGE_ROOT.exists():
        STORAGE_ROOT.mkdir(parents=True)


def calculate_file_hash(file_path: str) -> str:
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


def save_file_version(src_path: str) -> dict:
    """
    Copies the file to the storage directory using Content-Addressed Storage.

    METHOD 1 IMPLEMENTATION:
    1. Calculate the unique fingerprint (SHA-256 hash) of the file content.
    2. Check if we already have a file with that fingerprint in our storage.
    3. If YES (Deduplication): We skip writing the file again! We just return the path to the existing one.
       This saves massive space for duplicate files (e.g., File A and Copy of File A).
    4. If NO (New Content): We compress the file (GZIP) to save more space and write it.
    """
    init_storage()

    if not os.path.exists(src_path):
        return None

    # Step 1: Calculate the unique fingerprint (Hash)
    file_hash = calculate_file_hash(src_path)
    file_size = os.path.getsize(src_path)

    # Step 2: Define the storage filename based on the Hash
    # We use .gz extension because we will compress it
    storage_filename = f"{file_hash}.gz"
    storage_path = STORAGE_ROOT / storage_filename

    # Step 3: Deduplication Check
    if storage_path.exists():
        # COMMENT: We found a file with the exact same content!
        # No need to copy/write anything. We just return its location.
        print(
            f"[Storage] Deduplication hit! Content {file_hash[:8]}... already exists."
        )
        return {
            "storage_path": str(storage_path),
            "file_hash": file_hash,
            "file_size": file_size,
            "filename": storage_filename,
        }

    # Step 4: Write new content (Compressed)
    try:
        # COMMENT: Reading the source file...
        with open(src_path, "rb") as f_in:
            # COMMENT: ...and writing it into a compressed GZIP file
            with gzip.open(storage_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        print(f"[Storage] Saved new content: {storage_filename}")

        return {
            "storage_path": str(storage_path),
            "file_hash": file_hash,
            "file_size": file_size,  # This is original size
            "filename": storage_filename,
        }
    except Exception as e:
        print(f"[Storage] Failed to backup file {src_path}: {e}")
        # Cleanup partial write if failed
        if storage_path.exists():
            try:
                os.remove(storage_path)
            except:
                pass
        return None


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

        # Check if the stored file is one of our new compressed ones
        if str(storage_path).endswith(".gz"):
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
        for f in STORAGE_ROOT.glob("*"):
            if f.is_file():
                total_size += f.stat().st_size
    return total_size


def run_garbage_collection(active_file_hashes: set):
    """
    Simple Garbage Collection (GC).

    1. Look at all files in our storage folder.
    2. Check if their Hash is in the 'active_file_hashes' list (from the Database).
    3. If a file is NOT in the list, it means no version uses this content anymore.
    4. Delete the unused file to free up space.
    """
    print("[Storage] Starting Garbage Collection...")
    cleaned_count = 0
    cleaned_bytes = 0

    if not STORAGE_ROOT.exists():
        return

    for stored_file in STORAGE_ROOT.iterdir():
        # Check if it's a file
        if stored_file.is_file():
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
