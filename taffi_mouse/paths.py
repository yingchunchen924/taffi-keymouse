from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from . import APP_DATA_DIR_NAME


PROJECT_DIR = Path(__file__).resolve().parents[1]
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", str(PROJECT_DIR)))
ASSET_DIR = RESOURCE_DIR / "assets"
ICON_ICO = ASSET_DIR / "taffi_icon.ico"
ICON_PNG = ASSET_DIR / "taffi_icon.png"

APPDATA_DIR = Path(os.environ.get("APPDATA", str(Path.home())))
DATA_DIR = APPDATA_DIR / APP_DATA_DIR_NAME
LEGACY_DATA_DIRS = (
    APPDATA_DIR / "塔菲键鼠3.0",
    APPDATA_DIR / "塔菲键鼠",
)
DB_PATH = DATA_DIR / "taffi3.db"
SCRIPT_DIR = DATA_DIR / "scripts"
REF_DIR = DATA_DIR / "refs"
LOG_DIR = DATA_DIR / "logs"
EXPORT_DIR = DATA_DIR / "exports"


def _find_legacy_data_dir() -> Optional[Path]:
    for candidate in LEGACY_DATA_DIRS:
        if candidate == DATA_DIR:
            continue
        if (candidate / "taffi3.db").exists():
            return candidate
    return None


def _migrate_legacy_data() -> Optional[Path]:
    if DB_PATH.exists():
        return None
    source = _find_legacy_data_dir()
    if not source:
        return None
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = DATA_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    _rewrite_migrated_reference_paths(source)
    return source


def _rewrite_migrated_reference_paths(source: Path) -> None:
    if not DB_PATH.exists():
        return
    source_root = source.resolve()
    data_root = DATA_DIR.resolve()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT id, file_path FROM reference_images").fetchall()
        for reference_id, file_path in rows:
            try:
                relative_path = Path(file_path).resolve().relative_to(source_root)
            except (OSError, ValueError):
                continue
            new_path = data_root / relative_path
            conn.execute(
                "UPDATE reference_images SET file_path=? WHERE id=?",
                (str(new_path), reference_id),
            )


def ensure_dirs() -> None:
    _migrate_legacy_data()
    for path in (DATA_DIR, SCRIPT_DIR, REF_DIR, LOG_DIR, EXPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)
