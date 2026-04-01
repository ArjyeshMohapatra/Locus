# type: ignore

import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from PIL import ImageGrab

from app.database import crud
from app.database.models import SessionLocal

PRIVATE_BROWSING_MARKERS = ("incognito", "inprivate", "private browsing")
DEFAULT_INTERVAL_SECONDS = 10
DEFAULT_RETENTION_DAYS = 10
DEFAULT_HISTORY_LIMIT = 100
UNIGET_ALIAS_COMMANDS = ("unigetui", "wingetui", "unigetui.exe", "wingetui.exe")
LEARNING_STATE_KEY = "snapshot_learning_state"
LEARNING_MAX_WEIGHT = 8.0
MIN_SNAPSHOT_UNLOCK_COMPAT_LENGTH = 4
MIN_SNAPSHOT_PASSPHRASE_LENGTH = 12
UNSAFE_LAUNCH_CHARS_RE = re.compile(r"[&;<>`\"'|\r\n\t]")
SAFE_COMMAND_CANDIDATE_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
NON_ALNUM_LOWER_RE = re.compile(r"[^a-z0-9]+")
logger = logging.getLogger(__name__)
_WINDOW_CAPTURE_WARNING_EMITTED = False


def _resolve_snapshot_image_root() -> Path:
    explicit_images_dir = os.getenv("LOCUS_SNAPSHOT_IMAGE_DIR", "").strip()
    if explicit_images_dir:
        return Path(explicit_images_dir).expanduser().resolve()

    explicit_data_dir = os.getenv("LOCUS_DATA_DIR", "").strip()
    if explicit_data_dir:
        return Path(explicit_data_dir).expanduser().resolve() / "snapshot_images"

    if getattr(sys, "frozen", False):
        if os.name == "nt":
            base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        elif sys.platform == "darwin":
            base = str(Path.home() / "Library" / "Application Support")
        else:
            base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")

        return Path(base).expanduser().resolve() / "locus" / "snapshot_images"

    return Path("./.locus_snapshot_images").resolve()


SNAPSHOT_IMAGE_ROOT = _resolve_snapshot_image_root()

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Coding": ("code", "pycharm", "visual studio", "terminal", "github", "git"),
    "Communication": ("telegram", "discord", "slack", "teams", "whatsapp", "mail"),
    "Browsing": ("chrome", "firefox", "edge", "safari", "browser", "http"),
    "Learning": (
        "docs",
        "tutorial",
        "course",
        "leetcode",
        "stack overflow",
        "wikipedia",
    ),
    "Media": ("youtube", "spotify", "netflix", "vlc", "music", "video"),
    "Files": ("explorer", "folder", "file", "drive", "onedrive", "desktop"),
}

# Map window/app labels to concrete launch commands that are likely to exist on Windows.
APP_LAUNCH_ALIASES: dict[str, tuple[str, ...]] = {
    "visual studio code": ("code", "Code.exe"),
    "code": ("code", "Code.exe"),
    "chrome": ("google-chrome", "chrome", "chrome.exe"),
    "google chrome": ("google-chrome", "chrome", "chrome.exe"),
    "edge": ("msedge", "msedge.exe"),
    "microsoft edge": ("msedge", "msedge.exe"),
    "firefox": ("firefox", "firefox.exe"),
    "file explorer": ("xdg-open", "nautilus", "explorer", "explorer.exe"),
    "explorer": ("xdg-open", "nautilus", "explorer", "explorer.exe"),
    "windows terminal": (
        "gnome-terminal",
        "konsole",
        "wt",
        "wt.exe",
        "powershell",
        "powershell.exe",
    ),
    "terminal": (
        "gnome-terminal",
        "konsole",
        "wt",
        "wt.exe",
        "powershell",
        "powershell.exe",
        "cmd",
    ),
    "command prompt": ("gnome-terminal", "cmd", "cmd.exe"),
    "notepad": ("gedit", "kate", "notepad", "notepad.exe"),
    "uniget": UNIGET_ALIAS_COMMANDS,
    "unigetui": UNIGET_ALIAS_COMMANDS,
    "wingetui": UNIGET_ALIAS_COMMANDS,
}

STOP_WORDS = {
    "the",
    "a",
    "an",
    "is",
    "was",
    "were",
    "i",
    "me",
    "my",
    "at",
    "on",
    "in",
    "to",
    "of",
    "for",
    "what",
    "doing",
    "did",
    "yesterday",
    "today",
}

LINUX_APP_LABEL_ALIASES = {
    "brave-browser": "Brave",
    "brave": "Brave",
    "google-chrome": "Google Chrome",
    "chromium-browser": "Chromium",
    "chromium": "Chromium",
    "firefox": "Firefox",
    "microsoft-edge": "Microsoft Edge",
    "msedge": "Microsoft Edge",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
}

SELF_WINDOW_APP_MARKERS = {
    "locus",
    "locus_tauri",
    "com.locus.app",
}


class SnapshotService:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._fernet: Fernet | None = None
        self._last_fingerprint: str | None = None
        self._desktop_app_index: dict[str, str] = {}
        self._desktop_app_index_ready = False
        SNAPSHOT_IMAGE_ROOT.mkdir(parents=True, exist_ok=True)

    def _ensure_desktop_app_index(self) -> None:
        if os.name != "posix" or self._desktop_app_index_ready:
            return

        with self._lock:
            if self._desktop_app_index_ready:
                return
            started = time.monotonic()
            self._build_desktop_app_index()
            self._desktop_app_index_ready = True
            logger.info(
                "[SnapshotService] Loaded desktop launcher index: %s entries in %.2fs",
                len(self._desktop_app_index),
                time.monotonic() - started,
            )

    def _build_desktop_app_index(self) -> None:
        if os.name != "posix":
            return

        self._desktop_app_index.clear()

        search_dirs = [
            "/usr/share/applications",
            "/usr/local/share/applications",
            "/var/lib/flatpak/exports/share/applications",
            "/var/lib/snapd/desktop/applications",
            os.path.expanduser("~/.local/share/applications"),
            os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
            "/snap/share/applications"
        ]

        for d in search_dirs:
            if not os.path.exists(d):
                continue

            for root, _, files in os.walk(d):
                for f in files:
                    if f.endswith(".desktop"):
                        try:
                            name = None
                            exec_cmd = None
                            with open(os.path.join(root, f), "r", encoding="utf-8", errors="ignore") as fh:
                                in_desktop_entry = False
                                for line in fh:
                                    line = line.strip()
                                    if line == "[Desktop Entry]":
                                        in_desktop_entry = True
                                        continue
                                    elif line.startswith("[") and in_desktop_entry:
                                        break

                                    if in_desktop_entry:
                                        if line.startswith("Name=") and not name:
                                            name = line[5:].strip()
                                        elif line.startswith("Exec=") and not exec_cmd:
                                            exec_cmd = line[5:].strip()

                                        if name and exec_cmd:
                                            break

                            if name and exec_cmd:
                                clean_exec = re.sub(r'%[a-zA-Z]', '', exec_cmd).strip()
                                self._desktop_app_index[name.lower()] = clean_exec
                        except Exception as e:
                            logger.debug(f"[SnapshotService] Failed to parse desktop file {f}: {e}")

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            # Note: _cleanup_orphaned_images is now deferred until unlock()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def is_unlocked(self) -> bool:
        with self._lock:
            return self._fernet is not None

    def lock(self) -> None:
        with self._lock:
            self._fernet = None

    def setup_master_password(self, master_passphrase: str, db) -> str:
        import secrets
        import base64

        master_passphrase = (master_passphrase or "").strip()
        if len(master_passphrase) < MIN_SNAPSHOT_PASSPHRASE_LENGTH:
            raise ValueError("Passphrase too short")

        recovery_passphrase = secrets.token_hex(16)

        data_key = Fernet.generate_key()
        candidate = Fernet(data_key)

        salt_master = os.urandom(16)
        wrap_key_master = self._derive_fernet_key(master_passphrase, salt_master)
        wrapped_master = Fernet(wrap_key_master).encrypt(data_key).decode("utf-8")

        salt_recovery = os.urandom(16)
        wrap_key_recovery = self._derive_fernet_key(recovery_passphrase, salt_recovery)
        wrapped_recovery = Fernet(wrap_key_recovery).encrypt(data_key).decode("utf-8")

        crud.set_setting(
            db,
            "snapshot_salt_master",
            base64.urlsafe_b64encode(salt_master).decode("ascii"),
        )
        crud.set_setting(db, "snapshot_wrapped_key_master", wrapped_master)
        crud.set_setting(
            db,
            "snapshot_salt_recovery",
            base64.urlsafe_b64encode(salt_recovery).decode("ascii"),
        )
        crud.set_setting(db, "snapshot_wrapped_key_recovery", wrapped_recovery)
        crud.set_setting(db, "snapshot_key_verifier", "v2-wrapped")

        # Auto-enable snapshots once the vault is set up
        crud.set_setting(db, "snapshot_enabled", "true")

        with self._lock:
            self._fernet = candidate
        return recovery_passphrase

    def unlock(self, passphrase: str, db) -> bool:
        passphrase = (passphrase or "").strip()
        if not passphrase:
            return False

        verifier = crud.get_setting(db, "snapshot_key_verifier", None)

        # V2 Logic
        if verifier == "v2-wrapped":
            import base64

            # Try Master
            try:
                salt_b64 = crud.get_setting(db, "snapshot_salt_master", "")
                salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
                wrap_key = self._derive_fernet_key(passphrase, salt)
                wrapped_data_key = crud.get_setting(
                    db, "snapshot_wrapped_key_master", ""
                )
                data_key = Fernet(wrap_key).decrypt(wrapped_data_key.encode("utf-8"))
                with self._lock:
                    self._fernet = Fernet(data_key)
                self._trigger_orphaned_cleanup(db)
                return True
            except Exception:  # nosec B110
                # Failed to unlock with master passphrase (expected if wrong)
                pass

            # Try Recovery
            try:
                salt_b64 = crud.get_setting(db, "snapshot_salt_recovery", "")
                salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
                wrap_key = self._derive_fernet_key(passphrase, salt)
                wrapped_data_key = crud.get_setting(
                    db, "snapshot_wrapped_key_recovery", ""
                )
                data_key = Fernet(wrap_key).decrypt(wrapped_data_key.encode("utf-8"))
                with self._lock:
                    self._fernet = Fernet(data_key)
                self._trigger_orphaned_cleanup(db)
                return True
            except Exception:  # nosec B110
                # Failed to unlock with recovery passphrase (expected if wrong)
                pass

            return False

        # Legacy V1 Logic
        if len(passphrase) < MIN_SNAPSHOT_UNLOCK_COMPAT_LENGTH:
            return False
        has_existing_key = bool((verifier or "").strip())
        if not has_existing_key and len(passphrase) < MIN_SNAPSHOT_PASSPHRASE_LENGTH:
            return False

        import base64

        salt_b64 = crud.get_setting(db, "snapshot_salt", None)
        if not salt_b64:
            return False

        try:
            salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        except Exception:
            salt = b"locus-default-salt"

        key = self._derive_fernet_key(passphrase, salt)
        candidate = Fernet(key)

        if verifier:
            try:
                plain = candidate.decrypt(verifier.encode("utf-8")).decode("utf-8")
                if plain != "locus-snapshot-key-v1":
                    return False
            except Exception:
                return False

        with self._lock:
            self._fernet = candidate

        self._trigger_orphaned_cleanup(db)
        return True

    def get_settings(self, db) -> dict[str, Any]:
        settings = {
            "enabled": self._to_bool(crud.get_setting(db, "snapshot_enabled", "false")),
            "interval_seconds": self._to_int(
                crud.get_setting(
                    db, "snapshot_interval_seconds", str(DEFAULT_INTERVAL_SECONDS)
                ),
                DEFAULT_INTERVAL_SECONDS,
                min_value=5,
                max_value=300,
            ),
            "retention_days": self._to_int(
                crud.get_setting(
                    db, "snapshot_retention_days", str(DEFAULT_RETENTION_DAYS)
                ),
                DEFAULT_RETENTION_DAYS,
                min_value=1,
                max_value=365,
            ),
            "exclude_private_browsing": self._to_bool(
                crud.get_setting(db, "snapshot_exclude_private_browsing", "true")
            ),
            "capture_on_window_change": self._to_bool(
                crud.get_setting(db, "snapshot_capture_on_window_change", "true")
            ),
            "allow_individual_delete": self._to_bool(
                crud.get_setting(db, "snapshot_allow_delete", "false")
            ),
            "unlocked": self.is_unlocked(),
            "has_existing_key": bool(
                (crud.get_setting(db, "snapshot_key_verifier", None) or "").strip()
            ),
        }
        return settings

    def save_settings(self, db, updates: dict[str, Any]) -> dict[str, Any]:
        if "enabled" in updates:
            crud.set_setting(
                db, "snapshot_enabled", "true" if updates["enabled"] else "false"
            )
        if "interval_seconds" in updates:
            crud.set_setting(
                db, "snapshot_interval_seconds", str(int(updates["interval_seconds"]))
            )
        if "retention_days" in updates:
            crud.set_setting(
                db, "snapshot_retention_days", str(int(updates["retention_days"]))
            )
        if "exclude_private_browsing" in updates:
            crud.set_setting(
                db,
                "snapshot_exclude_private_browsing",
                "true" if updates["exclude_private_browsing"] else "false",
            )
        if "capture_on_window_change" in updates:
            crud.set_setting(
                db,
                "snapshot_capture_on_window_change",
                "true" if updates["capture_on_window_change"] else "false",
            )
        if "allow_individual_delete" in updates:
            crud.set_setting(
                db,
                "snapshot_allow_delete",
                "true" if updates["allow_individual_delete"] else "false",
            )
        return self.get_settings(db)

    def query(self, db, query_text: str, limit: int = 200) -> dict[str, Any]:
        start_time, end_time = self._resolve_time_window(query_text)
        rows = crud.get_activity_snapshots(
            db, start_time, end_time, limit=max(limit, 50)
        )
        learning_state = self._load_learning_state(db)

        tokens = self._query_tokens(query_text)
        requested_category = self._infer_requested_category(query_text)
        items: list[dict[str, Any]] = []

        for row in rows:
            payload = self._decrypt_payload(row.encrypted_payload)
            if payload is None:
                continue

            category = str(payload.get("category") or self._categorize(payload))
            score = self._score_item(
                payload,
                tokens,
                requested_category,
                learning_state,
            )
            if score <= 0 and not self._is_generic_query(query_text):
                continue

            action = self._build_action(payload)
            items.append(
                {
                    "id": row.id,
                    "captured_at": str(payload.get("captured_at") or row.captured_at),
                    "window_title": payload.get("window_title"),
                    "app_name": payload.get("app_name"),
                    "url": payload.get("url"),
                    "file_path": payload.get("file_path"),
                    "category": category,
                    "image_available": bool(payload.get("image_path")),
                    "image_endpoint": f"/snapshots/{row.id}/image",
                    "action": action,
                    "score": score,
                }
            )

        items.sort(key=lambda x: (x["score"], x["captured_at"]), reverse=True)
        top_items = items[:limit]

        app_buckets: dict[str, int] = {}
        category_buckets: dict[str, int] = {}
        for item in top_items:
            app_name = item.get("app_name") or "Unknown"
            app_buckets[app_name] = app_buckets.get(app_name, 0) + 1
            category = str(item.get("category") or "Other")
            category_buckets[category] = category_buckets.get(category, 0) + 1

        highlights = self._build_highlights(top_items)
        general_answer = self._answer_general_time_query(query_text)
        if general_answer:
            highlights.insert(0, general_answer)

        return {
            "query": query_text,
            "window_start": start_time.isoformat() if start_time else None,
            "window_end": end_time.isoformat() if end_time else None,
            "count": len(top_items),
            "summary": {
                "top_apps": sorted(
                    app_buckets.items(), key=lambda x: x[1], reverse=True
                )[:5],
                "top_categories": sorted(
                    category_buckets.items(), key=lambda x: x[1], reverse=True
                )[:5],
                "highlights": highlights,
                "learning": {
                    "enabled": True,
                    "feedback_events": int(learning_state.get("feedback_events", 0)),
                },
            },
            "items": top_items,
        }

    def history(
        self,
        db,
        text_query: str | None = None,
        category: str | None = None,
        app_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> dict[str, Any]:
        rows = crud.get_activity_snapshots(
            db,
            start_time=start_time,
            end_time=end_time,
            limit=max(1, min(1000, int(limit))),
        )

        query_tokens = self._query_tokens(text_query or "")
        category_filter = (category or "").strip().lower()
        app_filter = (app_name or "").strip().lower()

        items: list[dict[str, Any]] = []
        category_buckets: dict[str, int] = {}
        app_buckets: dict[str, int] = {}

        for row in rows:
            item = self._history_item_from_row(
                row,
                category_filter=category_filter,
                app_filter=app_filter,
                query_tokens=query_tokens,
            )
            if item is None:
                continue

            category_label = str(item.get("category") or "Other")
            app_value = str(item.get("app_name") or "Unknown")
            category_buckets[category_label] = (
                category_buckets.get(category_label, 0) + 1
            )
            app_buckets[app_value] = app_buckets.get(app_value, 0) + 1
            items.append(item)

        items.sort(key=lambda item: item.get("captured_at", ""), reverse=True)

        return {
            "count": len(items),
            "items": items,
            "filters": {
                "query": text_query or "",
                "category": category or "",
                "app_name": app_name or "",
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
            },
            "facets": {
                "categories": sorted(
                    category_buckets.items(), key=lambda x: x[1], reverse=True
                ),
                "apps": sorted(app_buckets.items(), key=lambda x: x[1], reverse=True)[
                    :20
                ],
            },
        }

    def execute_action(self, action_type: str, value: str) -> dict[str, Any]:
        clean_type = (action_type or "").strip().lower()
        clean_value = (value or "").strip()
        if not clean_value:
            return {"ok": False, "message": "Action value is required"}

        try:
            if clean_type == "open_url":
                webbrowser.open(clean_value, new=2)
                return {"ok": True, "message": "URL opened", "type": clean_type}

            if clean_type == "open_file":
                if not os.path.exists(clean_value):
                    return {"ok": False, "message": "File/path does not exist"}
                import sys
                import subprocess  # nosec B404

                if sys.platform == "win32":
                    os.startfile(clean_value)  # nosec B606
                elif sys.platform == "darwin":
                    subprocess.run(["open", clean_value])  # nosec B603 B607
                else:
                    subprocess.run(["xdg-open", clean_value])  # nosec B603 B607
                return {"ok": True, "message": "File/path opened", "type": clean_type}

            if clean_type in ("open_app", "launch_app"):
                if clean_value == "unknown":
                    return {
                        "ok": False,
                        "message": (
                            "Cannot launch an unidentified application from snapshot metadata. "
                            "Install optional Linux helpers like kdotool, xdotool, or xprop to improve app identification."
                        ),
                    }
                launch_result = self._launch_app(clean_value)
                if not launch_result["ok"]:
                    return {
                        "ok": False,
                        "message": launch_result["message"],
                        "type": clean_type,
                    }
                return {
                    "ok": True,
                    "message": launch_result["message"],
                    "type": clean_type,
                }

            return {"ok": False, "message": f"Unsupported action type: {clean_type}"}
        except Exception as exc:
            return {"ok": False, "message": str(exc), "type": clean_type}

    def record_feedback(
        self,
        db,
        query_text: str,
        snapshot_id: int | None,
        helpful: bool,
        action_type: str | None = None,
    ) -> dict[str, Any]:
        state = self._load_learning_state(db)
        delta = 1.0 if helpful else -1.0

        self._apply_query_feedback(state, query_text, delta)
        payload = self._load_snapshot_payload_for_feedback(db, snapshot_id)
        self._apply_snapshot_feedback(state, payload, delta)
        self._apply_action_feedback(state, action_type, delta)

        state["feedback_events"] = int(state.get("feedback_events", 0)) + 1
        self._save_learning_state(db, state)

        return {
            "ok": True,
            "feedback_events": state["feedback_events"],
        }

    def _load_snapshot_payload_for_feedback(
        self, db, snapshot_id: int | None
    ) -> dict[str, Any] | None:
        if not snapshot_id:
            return None
        row = crud.get_activity_snapshot_by_id(db, snapshot_id)
        if row is None:
            return None
        return self._decrypt_payload(row.encrypted_payload)

    def _apply_query_feedback(
        self, state: dict[str, Any], query_text: str, delta: float
    ) -> None:
        for token in self._query_tokens(query_text):
            self._update_weight(state["token_weights"], token, 0.2 * delta)

    def _apply_snapshot_feedback(
        self,
        state: dict[str, Any],
        payload: dict[str, Any] | None,
        delta: float,
    ) -> None:
        if not payload:
            return

        app_name = self._normalize_app_label(str(payload.get("app_name") or ""))
        if app_name:
            self._update_weight(state["app_weights"], app_name, 0.8 * delta)

        category = str(payload.get("category") or self._categorize(payload)).lower()
        if category:
            self._update_weight(state["category_weights"], category, 0.6 * delta)

        domain = self._extract_domain(payload.get("url"))
        if domain:
            self._update_weight(state["domain_weights"], domain, 0.7 * delta)

    def _apply_action_feedback(
        self,
        state: dict[str, Any],
        action_type: str | None,
        delta: float,
    ) -> None:
        action_key = str(action_type or "").strip().lower()
        if action_key:
            self._update_weight(state["action_weights"], action_key, 0.5 * delta)

    def _launch_app(self, raw_value: str) -> dict[str, Any]:
        value = (raw_value or "").strip()
        if not value:
            return {"ok": False, "message": "Action value is required"}

        if SnapshotService._contains_unsafe_launch_chars(value):
            return {
                "ok": False,
                "message": "Unsafe launch value rejected",
            }

        self._ensure_desktop_app_index()

        normalized_label = SnapshotService._normalize_app_label(value)

        # Direct executable/file path.
        if os.path.exists(value):
            os.startfile(value)  # nosec B606
            return {"ok": True, "message": f"Opened {value}"}

        # Check Universal .desktop Index first
        if normalized_label.lower() in getattr(self, "_desktop_app_index", {}):
            try:
                cmd = self._desktop_app_index[normalized_label.lower()]
                import shlex
                import subprocess  # nosec B404

                subprocess.Popen(shlex.split(cmd), close_fds=True)  # nosec B603
                return {
                    "ok": True,
                    "message": "Application launch requested via .desktop shortcut",
                }
            except Exception as e:
                logger.error(f"[SnapshotService] Failed to launch via .desktop: {e}")

        candidates = SnapshotService._build_app_launch_candidates(
            value, normalized_label
        )
        for candidate in candidates:
            if SnapshotService._try_launch_candidate(candidate):
                return {
                    "ok": True,
                    "message": f"Application launch requested via '{candidate}'",
                }

        return {
            "ok": False,
            "message": (
                f"Could not launch app '{value}'. The snapshot stores a window/app label, "
                "which may not be a runnable command on this machine."
            ),
        }

    @staticmethod
    def _build_app_launch_candidates(
        raw_value: str, normalized_value: str
    ) -> list[str]:
        normalized = (normalized_value or "").lower().strip()
        raw = (raw_value or "").strip()

        candidates: list[str] = []
        candidates.extend(APP_LAUNCH_ALIASES.get(normalized, ()))
        if raw.lower() != normalized:
            candidates.extend(APP_LAUNCH_ALIASES.get(raw.lower(), ()))

        first_token = normalized.split(" ")[0] if normalized else ""
        if first_token:
            candidates.extend(APP_LAUNCH_ALIASES.get(first_token, ()))
            candidates.append(first_token)
        if normalized:
            candidates.append(normalized)

        compact = NON_ALNUM_LOWER_RE.sub("", normalized)
        if compact:
            candidates.append(compact)

        # Keep only safe command-like candidates and de-duplicate.
        raw_first_token = raw.split()[0] if raw else ""
        if raw_first_token:
            candidates.append(raw_first_token)

        # Preserve order while de-duplicating.
        seen: set[str] = set()
        unique: list[str] = []
        for candidate in candidates:
            c = candidate.strip()
            k = c.lower()
            if not c or k in seen or not SnapshotService._is_safe_command_candidate(c):
                continue
            seen.add(k)
            unique.append(c)
        return unique

    @staticmethod
    def _try_launch_candidate(candidate: str) -> bool:
        if not SnapshotService._is_safe_command_candidate(candidate):
            return False
        try:
            resolved = shutil.which(candidate)
            if resolved:
                subprocess.Popen([resolved], close_fds=True)  # nosec B603
            else:
                subprocess.Popen([candidate], close_fds=True)  # nosec B603
            return True
        except Exception:
            return False

    @staticmethod
    def _contains_unsafe_launch_chars(value: str) -> bool:
        return bool(UNSAFE_LAUNCH_CHARS_RE.search((value or "").strip()))

    @staticmethod
    def _is_safe_command_candidate(value: str) -> bool:
        return bool(SAFE_COMMAND_CANDIDATE_RE.fullmatch((value or "").strip()))

    @staticmethod
    def _normalize_app_label(raw_value: str) -> str:
        value = (raw_value or "").strip().lower()
        if not value:
            return value
        # Strip trailing version fragments: "UniGetUI 3.7.7" -> "unigetui"
        value = re.sub(r"\s+v?\d+(?:\.\d+){1,3}.*$", "", value).strip()
        value = re.sub(r"\s+\(\d+(?:\.\d+){1,3}.*\)$", "", value).strip()
        value = re.sub(r"\s+version\s+\d+(?:\.\d+){1,3}.*$", "", value).strip()
        return value

    def get_snapshot_image_bytes(self, db, snapshot_id: int) -> bytes | None:
        row = crud.get_activity_snapshot_by_id(db, snapshot_id)
        if not row:
            return None
        payload = self._decrypt_payload(row.encrypted_payload)
        if payload is None:
            return None
        image_path = str(payload.get("image_path") or "").strip()
        if not image_path:
            return None
        normalized = os.path.abspath(image_path)
        root = str(SNAPSHOT_IMAGE_ROOT.resolve())
        try:
            if os.path.commonpath([normalized, root]) != root:
                return None
        except ValueError:
            return None
        if not os.path.exists(normalized):
            return None

        try:
            with open(normalized, "rb") as fh:
                raw = fh.read()
        except Exception:
            return None

        # Backward compatibility: old snapshots may still reference plaintext JPEG files.
        if normalized.lower().endswith((".jpg", ".jpeg")):
            return raw

        return self._decrypt_image_bytes(raw)

    def delete_snapshot(self, db, snapshot_id: int) -> bool:
        row = crud.get_activity_snapshot_by_id(db, snapshot_id)
        if not row:
            return False

        payload = self._decrypt_payload(row.encrypted_payload)
        if payload is not None:
            self._remove_snapshot_image(payload.get("image_path"))

        return crud.delete_activity_snapshot(db, snapshot_id)

    def reset_passphrase(self, db) -> dict[str, Any]:
        deleted_count = crud.delete_all_activity_snapshots(db)
        self._wipe_snapshot_images_dir()

        # Clear key material so next unlock/enabling establishes a new passphrase.
        crud.set_setting(db, "snapshot_salt", "")
        crud.set_setting(db, "snapshot_key_verifier", "")
        crud.set_setting(db, "snapshot_enabled", "false")
        crud.set_setting(db, LEARNING_STATE_KEY, "")

        self.lock()
        self._last_fingerprint = None

        return {
            "ok": True,
            "deleted_snapshots": deleted_count,
            "message": "Snapshot vault reset. All snapshots were removed.",
        }

    def _loop(self) -> None:
        self._last_capture_time = 0.0
        self._last_window_title_check = None
        next_cleanup_at = 0.0

        # On Linux, many tools (xdotool, ImageGrab) need a DISPLAY variable.
        if os.name == "posix" and "DISPLAY" not in os.environ:
            os.environ["DISPLAY"] = ":0"
            logger.info("[SnapshotService] Initialized DISPLAY=:0 for Linux")

        while not self._stop_event.is_set():
            now_monotonic = time.monotonic()
            db = SessionLocal()
            try:
                settings = self.get_settings(db)
                if not settings["enabled"]:
                    time.sleep(1.0)
                    continue

                if not self.is_unlocked():
                    if int(now_monotonic) % 60 == 0:
                        logger.info("[SnapshotService] Vault is locked, skipping loop")
                    time.sleep(1.0)
                    continue

                interval = max(5, int(settings["interval_seconds"]))

                # PROACTIVE CHECK: Get current title to see if it changed
                current_title = self._get_active_window_title()

                title_changed = (current_title != self._last_window_title_check)
                time_elapsed = (now_monotonic - self._last_capture_time) >= interval
                capture_on_window_change = bool(
                    settings.get("capture_on_window_change", True)
                )

                # Capture immediately on title changes; otherwise still capture on interval.
                # This allows periodic snapshots for tabs/web apps whose title may not change.
                if (
                    capture_on_window_change
                    and title_changed
                    and current_title
                    and current_title != "Unknown"
                ):
                    self._capture_once(
                        db,
                        settings,
                        current_title,
                        force_capture=False,
                    )
                    self._last_capture_time = now_monotonic
                    self._last_window_title_check = current_title
                elif time_elapsed:
                    self._capture_once(
                        db,
                        settings,
                        current_title,
                        force_capture=True,
                    )
                    self._last_capture_time = now_monotonic
                    self._last_window_title_check = current_title

                if now_monotonic >= next_cleanup_at:
                    self._cleanup_retention(db, int(settings["retention_days"]))
                    next_cleanup_at = now_monotonic + 3600.0
            except Exception as exc:
                logger.error(f"[SnapshotService] Loop error: {exc}")
            finally:
                db.close()

            time.sleep(2.0)  # Check window title every 2 seconds

    def _capture_once(
        self,
        db,
        settings: dict[str, Any],
        window_title: str | None = None,
        force_capture: bool = False,
    ) -> None:
        probe_payload: dict[str, Any] | None = None

        if window_title is None:
            window_title = self._get_active_window_title()

        if (
            sys.platform == "linux"
            and (not window_title or str(window_title).strip().lower() == "unknown")
        ):
            probe_payload = self._linux_probe_payload()
            if probe_payload:
                probe_title = str(
                    probe_payload.get("title")
                    or probe_payload.get("class")
                    or probe_payload.get("instance")
                    or ""
                ).strip()
                if probe_title:
                    window_title = probe_title

        if not window_title:
            window_title = "Unknown"

        if settings.get(
            "exclude_private_browsing", True
        ) and self._looks_private_browsing(window_title):
            return

        app_name = self._infer_app_name(window_title)
        if app_name == "Unknown" and sys.platform == "linux":
            app_name = self._infer_app_name_from_probe_payload(probe_payload)
        if self._is_locus_window(window_title, app_name):
            logger.debug("[SnapshotService] Skipping self-window capture")
            return

        url = self._infer_url(window_title)
        file_path = self._infer_file_path(window_title)

        fingerprint = hashlib.sha256(
            f"{window_title}|{app_name}|{url or ''}|{file_path or ''}".encode("utf-8")
        ).hexdigest()

        # ONLY take a screenshot if the context has changed
        # EXCEPTION: If the title is "Unknown" (ambiguous on Wayland), we always allow capture
        # because the interval timer is already handled in the _loop.
        if not force_capture and fingerprint == self._last_fingerprint and window_title != "Unknown":
            # We skip heavy screenshotting if nothing changed.
            logger.debug("[SnapshotService] Skipping capture: window context unchanged")
            return

        image_path = self._capture_screenshot()
        if not image_path:
            return

        captured_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "captured_at": captured_at,
            "window_title": window_title,
            "app_name": app_name,
            "url": url,
            "file_path": file_path,
            "image_path": image_path,
            "category": self._categorize(
                {
                    "window_title": window_title,
                    "app_name": app_name,
                    "url": url,
                    "file_path": file_path,
                }
            ),
        }

        encrypted_payload = self._encrypt_payload(payload)
        if not encrypted_payload:
            # Cleanup orphaned image if payload encryption fails
            if os.path.exists(image_path):
                os.remove(image_path)
            return

        try:
            crud.create_activity_snapshot(
                db,
                encrypted_payload=encrypted_payload,
                fingerprint=fingerprint,
                captured_at=datetime.now(timezone.utc),
            )
            self._last_fingerprint = fingerprint
        except Exception as e:
            # Cleanup orphaned image if DB insert fails
            if os.path.exists(image_path):
                os.remove(image_path)
            raise e

    def _trigger_orphaned_cleanup(self, db) -> None:
        try:
            self._cleanup_orphaned_images(db)
        except Exception as e:
            logger.error(f"Orphaned cleanup failed post-unlock: {e}")

    def _cleanup_orphaned_images(self, db) -> None:
        """Deletes any .enc files in SNAPSHOT_IMAGE_ROOT that are not referenced in the DB."""
        if not self.is_unlocked():
            logger.warning("[SnapshotService] Vault is locked; bypassing orphaned image cleanup to prevent data loss.")
            return

        from app.database import models

        if not SNAPSHOT_IMAGE_ROOT.exists():
            return

        print("[SnapshotService] Running orphaned image cleanup...")
        try:
            # Get all referenced paths from DB
            rows = db.query(models.ActivitySnapshotRecord).all()
            referenced_paths = set()
            for row in rows:
                try:
                    payload = self._decrypt_payload(row.encrypted_payload)
                    if payload and payload.get("image_path"):
                        referenced_paths.add(os.path.abspath(payload["image_path"]))
                except Exception as e:
                    logger.warning(f"Failed to decrypt/process snapshot record: {e}")
                    continue

            # Scan disk
            deleted_count = 0
            freed_bytes = 0
            for f in SNAPSHOT_IMAGE_ROOT.glob("*.enc"):
                abs_f = os.path.abspath(str(f))
                if abs_f not in referenced_paths:
                    try:
                        f_size = f.stat().st_size
                        f.unlink()
                        deleted_count += 1
                        freed_bytes += f_size
                    except Exception as e:
                        logger.warning(f"Failed to delete orphaned file {f}: {e}")
                        continue

            if deleted_count > 0:
                print(
                    f"[SnapshotService] GC: Cleaned up {deleted_count} orphaned snapshots ({freed_bytes / (1024*1024):.2f} MB)"
                )
        except Exception as e:
            print(f"[SnapshotService] GC: Orphaned cleanup failed: {e}")

    def _cleanup_retention(self, db, retention_days: int) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        old_rows = crud.get_activity_snapshots(
            db,
            end_time=cutoff,
            limit=5000,
        )
        for row in old_rows:
            payload = self._decrypt_payload(row.encrypted_payload)
            if payload is not None:
                self._remove_snapshot_image(payload.get("image_path"))
        crud.delete_activity_snapshots_before(db, cutoff)

    def _capture_screenshot(self) -> str | None:
        try:
            # On Linux, ImageGrab often needs a valid DISPLAY environment variable.
            if os.name == "posix" and "DISPLAY" not in os.environ:
                os.environ["DISPLAY"] = ":0"
                logger.debug("[SnapshotService] Forced DISPLAY=:0 for Linux capture")

            logger.info("[SnapshotService] Taking active screen capture...")
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            file_name = f"snap_{timestamp}_{uuid.uuid4().hex[:8]}.enc"
            target = SNAPSHOT_IMAGE_ROOT / file_name
            image = ImageGrab.grab(all_screens=True)
            if not image:
                return None
            image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=65, optimize=True)

            encrypted_bytes = self._encrypt_image_bytes(buffer.getvalue())
            if not encrypted_bytes:
                return None

            with open(target, "wb") as fh:
                fh.write(encrypted_bytes)
            return str(target)
        except Exception:
            logger.exception("Snapshot capture failed")
            return None

    @staticmethod
    def _remove_snapshot_image(image_path: Any) -> None:
        raw = str(image_path or "").strip()
        if not raw:
            return
        try:
            normalized = os.path.abspath(raw)
            root = str(SNAPSHOT_IMAGE_ROOT.resolve())
            try:
                if os.path.commonpath([normalized, root]) != root:
                    logger.warning(
                        "Refusing to remove snapshot image outside root: %s", normalized
                    )
                    return
            except ValueError:
                logger.warning(
                    "Refusing to remove snapshot image outside root: %s", normalized
                )
                return
            if os.path.exists(normalized):
                os.remove(normalized)
        except Exception:
            logger.exception("Failed to remove snapshot image: %s", raw)
            return

    @staticmethod
    def _wipe_snapshot_images_dir() -> None:
        try:
            if not SNAPSHOT_IMAGE_ROOT.exists():
                SNAPSHOT_IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
                os.chmod(SNAPSHOT_IMAGE_ROOT, 0o755)  # nosec B103
            for child in SNAPSHOT_IMAGE_ROOT.iterdir():
                try:
                    if child.is_file() or child.is_symlink():
                        child.unlink(missing_ok=True)
                except Exception:
                    logger.exception(
                        "Failed to remove snapshot image during vault wipe: %s", child
                    )
                    continue
        except Exception:
            logger.exception("Failed to wipe snapshot image directory")
            return

    def _encrypt_payload(self, payload: dict[str, Any]) -> str | None:
        with self._lock:
            if self._fernet is None:
                return None
            raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            return self._fernet.encrypt(raw).decode("utf-8")

    def _encrypt_image_bytes(self, image_bytes: bytes) -> bytes | None:
        with self._lock:
            if self._fernet is None:
                return None
            return self._fernet.encrypt(image_bytes)

    def _decrypt_payload(self, encrypted_payload: str) -> dict[str, Any] | None:
        with self._lock:
            if self._fernet is None:
                return None
            try:
                raw = self._fernet.decrypt(encrypted_payload.encode("utf-8"))
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return None

    def _decrypt_image_bytes(self, encrypted_bytes: bytes) -> bytes | None:
        with self._lock:
            if self._fernet is None:
                return None
            try:
                return self._fernet.decrypt(encrypted_bytes)
            except Exception:
                return None

    @staticmethod
    def _derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=390000,
        )
        key = kdf.derive(passphrase.encode("utf-8"))
        return base64.urlsafe_b64encode(key)

    @staticmethod
    def _to_bool(value: str | None) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _to_int(
        value: str | None,
        default: int,
        min_value: int,
        max_value: int,
    ) -> int:
        try:
            parsed = int(str(value))
        except Exception:
            return default
        return max(min_value, min(max_value, parsed))

    @staticmethod
    def _looks_private_browsing(window_title: str) -> bool:
        title = window_title.lower()
        return any(marker in title for marker in PRIVATE_BROWSING_MARKERS)

    @staticmethod
    def _is_locus_window(window_title: str, app_name: str) -> bool:
        title = str(window_title or "").strip().lower()
        app = str(app_name or "").strip().lower()

        if app in SELF_WINDOW_APP_MARKERS:
            return True
        if any(marker in app for marker in ("locus_tauri", "com.locus.app")):
            return True

        if title == "locus":
            return True
        if title.endswith(" - locus") or title.endswith(" | locus") or title.endswith(" • locus"):
            return True

        return False

    @staticmethod
    def _resolve_command_path(command_name: str) -> str | None:
        if not command_name:
            return None

        resolved = shutil.which(command_name)
        if resolved:
            return resolved

        home = Path.home()
        candidates = [
            home / ".cargo" / "bin" / command_name,
            home / ".local" / "bin" / command_name,
            Path("/usr/local/bin") / command_name,
            Path("/usr/bin") / command_name,
            Path("/bin") / command_name,
        ]
        for candidate in candidates:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)

        return None

    @staticmethod
    def _resolve_window_probe_command() -> str | None:
        explicit = os.getenv("LOCUS_WINDOW_PROBE", "").strip()
        if explicit:
            if os.path.isabs(explicit) and os.path.exists(explicit):
                return explicit
            resolved_explicit = SnapshotService._resolve_command_path(explicit)
            if resolved_explicit:
                return resolved_explicit

        from_path = SnapshotService._resolve_command_path("locus-window-probe")
        if from_path:
            return from_path

        # Dev and desktop build fallbacks when binary exists in local artifacts.
        repo_root = Path(__file__).resolve().parents[2]
        local_candidates = [
            repo_root
            / "tools"
            / "window-probe"
            / "target"
            / "release"
            / "locus-window-probe",
            repo_root
            / "tools"
            / "window-probe"
            / "target"
            / "x86_64-unknown-linux-gnu"
            / "release"
            / "locus-window-probe",
            repo_root
            / "src-tauri"
            / "binaries"
            / "locus-window-probe-x86_64-unknown-linux-gnu",
        ]
        for candidate in local_candidates:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)

        return None

    @staticmethod
    def _linux_probe_payload() -> dict[str, Any] | None:
        command = SnapshotService._resolve_window_probe_command()
        if not command:
            return None

        try:
            result = subprocess.run(  # nosec B603
                [command],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            output = (result.stdout or "").strip()
            if not output:
                return None

            payload = json.loads(output)
            if not isinstance(payload, dict) or not payload.get("ok"):
                return None
            return payload
        except Exception as e:
            logger.debug(f"[SnapshotService] bundled probe failed: {e}")
            return None

    @staticmethod
    def _linux_title_from_bundled_probe() -> str:
        payload = SnapshotService._linux_probe_payload()
        if not payload:
            return ""

        try:
            title = str(payload.get("title") or "").strip()
            app_class = str(payload.get("class") or payload.get("instance") or "").strip()

            if title:
                return title
            if app_class:
                return app_class
            return ""
        except Exception:
            return ""

    @staticmethod
    def _infer_app_name_from_probe_payload(payload: dict[str, Any] | None) -> str:
        if not payload:
            return "Unknown"

        class_name = str(payload.get("class") or "").strip()
        instance = str(payload.get("instance") or "").strip()
        for raw in (class_name, instance):
            if not raw:
                continue
            normalized = NON_ALNUM_LOWER_RE.sub("-", raw.lower()).strip("-")
            mapped = LINUX_APP_LABEL_ALIASES.get(normalized)
            if mapped:
                return mapped
            if raw.lower() not in {"unknown", "n/a"}:
                return raw

        return "Unknown"

    @staticmethod
    def _linux_title_from_kdotool() -> str:
        import subprocess  # nosec B404

        command = SnapshotService._resolve_command_path("kdotool")
        if not command:
            return ""

        try:
            result = subprocess.run(
                [command, "getactivewindow", "getwindowname"],  # nosec B603 B607
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            return result.stdout.strip()
        except Exception as e:
            logger.debug(f"[SnapshotService] kdotool failed: {e}")
            return ""

    @staticmethod
    def _linux_title_from_xdotool(is_wayland: bool) -> str:
        import subprocess  # nosec B404

        command = SnapshotService._resolve_command_path("xdotool")
        if not command:
            return ""

        try:
            result = subprocess.run(
                [command, "getactivewindow", "getwindowname"],  # nosec B603 B607
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            title = result.stdout.strip()
            # On Wayland, xdotool can return stale XWayland window titles.
            if is_wayland and "LOCUS" in title and "Visual Studio" not in title:
                return ""
            return title
        except Exception as e:
            logger.debug(f"[SnapshotService] xdotool failed: {e}")
            return ""

    @staticmethod
    def _linux_title_from_xprop() -> str:
        import subprocess  # nosec B404

        command = SnapshotService._resolve_command_path("xprop")
        if not command:
            return ""

        try:
            active_result = subprocess.run(
                [command, "-root", "_NET_ACTIVE_WINDOW"],  # nosec B603 B607
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            active_match = re.search(
                r"window id # (0x[0-9a-fA-F]+)",
                active_result.stdout,
            )
            active_id = active_match.group(1) if active_match else ""
            if not active_id or active_id == "0x0":
                return ""

            window_result = subprocess.run(
                [
                    command,
                    "-id",
                    active_id,
                    "_NET_WM_NAME",
                    "WM_NAME",
                    "WM_CLASS",
                ],  # nosec B603 B607
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )

            for line in window_result.stdout.splitlines():
                if "WM_NAME" not in line and "_NET_WM_NAME" not in line:
                    continue
                title_match = re.search(r'=\s*"(.*)"', line)
                if title_match and title_match.group(1).strip():
                    return title_match.group(1).strip()

            class_line = next(
                (
                    line
                    for line in window_result.stdout.splitlines()
                    if "WM_CLASS" in line
                ),
                "",
            )
            class_values = re.findall(r'"([^"]+)"', class_line)
            return class_values[-1].strip() if class_values else ""
        except Exception as e:
            logger.debug(f"[SnapshotService] xprop fallback failed: {e}")
            return ""

    @staticmethod
    def _get_active_window_title_linux() -> str:
        global _WINDOW_CAPTURE_WARNING_EMITTED
        is_wayland = (
            "wayland" in os.environ.get("WAYLAND_DISPLAY", "").lower()
            or "wayland" in os.environ.get("XDG_SESSION_TYPE", "").lower()
        )
        providers: list[Any] = [SnapshotService._linux_title_from_bundled_probe]
        if is_wayland:
            providers.append(SnapshotService._linux_title_from_kdotool)
        providers.append(lambda: SnapshotService._linux_title_from_xdotool(is_wayland))
        providers.append(SnapshotService._linux_title_from_xprop)

        try:
            for provider in providers:
                title = str(provider() or "").strip()
                if title:
                    return title
        except Exception as e:
            logger.error(f"[SnapshotService] Title capture error: {e}")
            return ""

        if not _WINDOW_CAPTURE_WARNING_EMITTED:
            logger.warning(
                "Failed to get active window. Install Linux helpers like kdotool (Wayland), xdotool (X11), or xprop for better app labels."
            )
            _WINDOW_CAPTURE_WARNING_EMITTED = True

        return ""

    @staticmethod
    def _get_active_window_title_windows() -> str:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return ""

            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return ""

            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value.strip()
        except Exception:
            logger.exception("Failed to capture active window title")
            return ""

    @staticmethod
    def _get_active_window_title() -> str:
        global _WINDOW_CAPTURE_WARNING_EMITTED
        import sys

        if sys.platform == "linux":
            return SnapshotService._get_active_window_title_linux()

        if os.name != "nt":
            if not _WINDOW_CAPTURE_WARNING_EMITTED:
                logger.warning(
                    "Active window title capture natively implemented for macOS is missing"
                )
                _WINDOW_CAPTURE_WARNING_EMITTED = True
            return ""

        return SnapshotService._get_active_window_title_windows()

    @staticmethod
    def _infer_app_name(window_title: str) -> str:
        parts = re.split(r"\s[-|–—•]\s", window_title)
        clean = [p.strip() for p in parts if p.strip()]
        if not clean:
            return "Unknown"

        candidate = clean[-1]
        normalized = NON_ALNUM_LOWER_RE.sub("-", candidate.lower()).strip("-")
        return LINUX_APP_LABEL_ALIASES.get(normalized, candidate)

    @staticmethod
    def _infer_url(window_title: str) -> str | None:
        match = re.search(r"https?://[^\s]+", window_title)
        if not match:
            return None
        return match.group(0)

    @staticmethod
    def _infer_file_path(window_title: str) -> str | None:
        match = re.search(r"[A-Za-z]:\\[^:*?\"<>|]+", window_title)
        if not match:
            return None
        return match.group(0).strip()

    @staticmethod
    def _searchable_blob(payload: dict[str, Any]) -> str:
        return " ".join(
            [
                str(payload.get("window_title", "")),
                str(payload.get("app_name", "")),
                str(payload.get("url", "")),
                str(payload.get("file_path", "")),
                str(payload.get("category", "")),
            ]
        ).lower()

    @staticmethod
    def _query_tokens(query_text: str) -> list[str]:
        tokens = [
            t
            for t in re.findall(r"[a-zA-Z0-9_.:/\\-]+", (query_text or "").lower())
            if t and t not in STOP_WORDS and len(t) > 1
        ]
        return tokens

    def _score_item(
        self,
        payload: dict[str, Any],
        query_tokens: list[str],
        requested_category: str | None,
        learning_state: dict[str, Any],
    ) -> float:
        searchable = self._searchable_blob(payload)
        category = str(payload.get("category") or self._categorize(payload))
        score = 1.0
        score += self._token_score(payload, searchable, query_tokens)
        score += self._learning_score(payload, query_tokens, learning_state)

        if requested_category and requested_category.lower() == category.lower():
            score += 3.0

        score += self._recency_boost(str(payload.get("captured_at") or ""))
        return round(score, 4)

    def _history_item_from_row(
        self,
        row,
        category_filter: str,
        app_filter: str,
        query_tokens: list[str],
    ) -> dict[str, Any] | None:
        payload = self._decrypt_payload(row.encrypted_payload)
        if payload is None:
            return None

        category_label = str(payload.get("category") or self._categorize(payload))
        payload["category"] = category_label
        app_value = str(payload.get("app_name") or "Unknown")
        searchable = self._searchable_blob(payload)

        if category_filter and category_label.lower() != category_filter:
            return None
        if app_filter and app_filter not in app_value.lower():
            return None
        if query_tokens and not all(tok in searchable for tok in query_tokens):
            return None

        # Ensure captured_at is UTC-aware before string conversion
        ts = payload.get("captured_at") or row.captured_at
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        elif isinstance(ts, str) and not (ts.endswith("Z") or "+00:00" in ts):
            ts += "Z"

        return {
            "id": row.id,
            "captured_at": str(ts),
            "window_title": payload.get("window_title"),
            "app_name": app_value,
            "url": payload.get("url"),
            "file_path": payload.get("file_path"),
            "category": category_label,
            "image_available": bool(payload.get("image_path")),
            "image_endpoint": f"/snapshots/{row.id}/image",
            "action": self._build_action(payload),
        }

    @staticmethod
    def _token_score(
        payload: dict[str, Any], searchable: str, query_tokens: list[str]
    ) -> float:
        if not query_tokens:
            return 0.0

        title = str(payload.get("window_title", "")).lower()
        app_name = str(payload.get("app_name", "")).lower()
        score = 0.0
        for token in query_tokens:
            if token in searchable:
                score += 2.0
            if token in title:
                score += 1.0
            if token in app_name:
                score += 1.0
        return score

    def _learning_score(
        self,
        payload: dict[str, Any],
        query_tokens: list[str],
        learning_state: dict[str, Any],
    ) -> float:
        score = 0.0
        token_weights = learning_state.get("token_weights", {})
        app_weights = learning_state.get("app_weights", {})
        category_weights = learning_state.get("category_weights", {})
        domain_weights = learning_state.get("domain_weights", {})

        app_name = self._normalize_app_label(str(payload.get("app_name") or ""))
        category = str(payload.get("category") or self._categorize(payload)).lower()
        domain = self._extract_domain(payload.get("url"))

        if app_name:
            score += float(app_weights.get(app_name, 0.0))
        if category:
            score += float(category_weights.get(category, 0.0))
        if domain:
            score += float(domain_weights.get(domain, 0.0))

        for token in query_tokens:
            score += float(token_weights.get(token, 0.0))

        return score

    @staticmethod
    def _extract_domain(url_value: Any) -> str | None:
        raw = str(url_value or "").strip()
        if not raw:
            return None
        try:
            parsed = urlparse(raw)
            host = (parsed.netloc or "").lower()
            host = host[4:] if host.startswith("www.") else host
            return host or None
        except Exception:
            return None

    @staticmethod
    def _default_learning_state() -> dict[str, Any]:
        return {
            "feedback_events": 0,
            "token_weights": {},
            "app_weights": {},
            "category_weights": {},
            "domain_weights": {},
            "action_weights": {},
        }

    def _load_learning_state(self, db) -> dict[str, Any]:
        raw = crud.get_setting(db, LEARNING_STATE_KEY, None)
        if not raw:
            return self._default_learning_state()
        try:
            data = json.loads(raw)
            base = self._default_learning_state()
            if isinstance(data, dict):
                for key in base.keys():
                    if key in data and isinstance(data[key], type(base[key])):
                        base[key] = data[key]
            return base
        except Exception:
            return self._default_learning_state()

    @staticmethod
    def _save_learning_state(db, state: dict[str, Any]) -> None:
        crud.set_setting(db, LEARNING_STATE_KEY, json.dumps(state, ensure_ascii=True))

    @staticmethod
    def _update_weight(weight_map: dict[str, float], key: str, delta: float) -> None:
        k = str(key or "").strip().lower()
        if not k:
            return
        current = float(weight_map.get(k, 0.0)) + float(delta)
        clamped = max(-LEARNING_MAX_WEIGHT, min(LEARNING_MAX_WEIGHT, current))
        if abs(clamped) < 0.05:
            weight_map.pop(k, None)
            return
        weight_map[k] = round(clamped, 4)

    @staticmethod
    def _recency_boost(captured_at_raw: str) -> float:
        try:
            captured_dt = datetime.fromisoformat(captured_at_raw.replace("Z", "+00:00"))
            age_hours = max(
                (datetime.now(timezone.utc) - captured_dt).total_seconds() / 3600.0,
                0.0,
            )
            return max(0.0, 3.0 - min(age_hours / 12.0, 3.0))
        except Exception:
            return 0.0

    def _infer_requested_category(self, query_text: str) -> str | None:
        q = (query_text or "").lower()
        for category, keywords in CATEGORY_KEYWORDS.items():
            if category.lower() in q:
                return category
            if any(keyword in q for keyword in keywords):
                return category
        return None

    def _categorize(self, payload: dict[str, Any]) -> str:
        blob = self._searchable_blob(payload)
        best_category = "Other"
        best_score = 0
        for category, keywords in CATEGORY_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in blob)
            if score > best_score:
                best_score = score
                best_category = category
        return best_category

    @staticmethod
    def _build_highlights(items: list[dict[str, Any]]) -> list[str]:
        if not items:
            return []
        highlights: list[str] = []
        first = items[0]
        highlights.append(
            f"Most relevant: {first.get('app_name') or 'Unknown'} at {first.get('captured_at')}"
        )

        unique_categories = {
            str(item.get("category") or "Other") for item in items[:25]
        }
        if unique_categories:
            highlights.append(
                f"Categories seen: {', '.join(sorted(unique_categories))}"
            )

        url_count = sum(1 for item in items if item.get("url"))
        if url_count:
            highlights.append(f"Web sessions identified: {url_count}")
        return highlights

    @staticmethod
    def _is_generic_query(query_text: str) -> bool:
        normalized = query_text.strip().lower()
        generic_markers = (
            "what was i doing",
            "what did i do",
            "yesterday",
            "today",
            "timeline",
            "activity",
        )
        return any(marker in normalized for marker in generic_markers)

    @staticmethod
    def _resolve_time_window(
        query_text: str,
    ) -> tuple[datetime | None, datetime | None]:
        now = datetime.now(timezone.utc)
        q = (query_text or "").strip().lower()

        if any(marker in q for marker in ("all time", "any time", "anytime", "ever")):
            return None, now

        if "yesterday" in q:
            day = (now - timedelta(days=1)).date()
            start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            end = datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc)
            return start, end

        if "today" in q:
            start = datetime.combine(
                now.date(), datetime.min.time(), tzinfo=timezone.utc
            )
            return start, now

        if "last week" in q:
            return now - timedelta(days=7), now

        if "this week" in q:
            start = datetime.combine(
                (now - timedelta(days=now.weekday())).date(),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            return start, now

        if "last month" in q:
            return now - timedelta(days=30), now

        if "this month" in q:
            start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
            return start, now

        match = re.search(
            r"last\s+(\d+)\s+(hour|hours|day|days|week|weeks|month|months)",
            q,
        )
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            if "hour" in unit:
                delta = timedelta(hours=amount)
            elif "day" in unit:
                delta = timedelta(days=amount)
            elif "week" in unit:
                delta = timedelta(days=7 * amount)
            else:
                delta = timedelta(days=30 * amount)
            return now - delta, now

        # Default to full retained history instead of only yesterday/today.
        return None, now

    @staticmethod
    def _answer_general_time_query(query_text: str) -> str | None:
        q = (query_text or "").strip().lower()
        if not q:
            return None

        now_local = datetime.now().astimezone()

        if any(token in q for token in ("what time is it", "current time", "time now")):
            return f"Current time: {now_local.strftime('%I:%M %p')}"

        if any(
            token in q
            for token in ("today date", "today's date", "what date", "date today")
        ):
            return f"Today's date: {now_local.strftime('%Y-%m-%d')}"

        if any(token in q for token in ("what day", "day today", "which day")):
            return f"Today is {now_local.strftime('%A')}"

        return None

    @staticmethod
    def _build_action(payload: dict[str, Any]) -> dict[str, str]:
        url = payload.get("url")
        file_path = payload.get("file_path")
        app_name = payload.get("app_name")

        if url:
            return {"type": "open_url", "label": "Open URL", "value": str(url)}
        if file_path:
            return {"type": "open_file", "label": "Open File", "value": str(file_path)}
        if app_name:
            app_lower = str(app_name).lower()
            # If we don't have a URL but the app is obviously a browser,
            # we should label it as a browser launch to prevent confusion over websites.
            is_browser = any(
                b in app_lower
                for b in ["chrome", "firefox", "brave", "edge", "safari", "opera"]
            )

            return {
                "type": "launch_app",
                "label": "Open Browser" if is_browser else "Open App",
                "value": SnapshotService._normalize_app_label(str(app_name)),
            }
        return {"type": "none", "label": "No action", "value": ""}


snapshot_service = SnapshotService()
