from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_NAME


PROJECT_DIR = Path(__file__).resolve().parents[1]
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", str(PROJECT_DIR)))
ASSET_DIR = RESOURCE_DIR / "assets"
ICON_ICO = ASSET_DIR / "taffi_icon.ico"
ICON_PNG = ASSET_DIR / "taffi_icon.png"

DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
DB_PATH = DATA_DIR / "taffi3.db"
SCRIPT_DIR = DATA_DIR / "scripts"
REF_DIR = DATA_DIR / "refs"
LOG_DIR = DATA_DIR / "logs"
EXPORT_DIR = DATA_DIR / "exports"


def ensure_dirs() -> None:
    for path in (DATA_DIR, SCRIPT_DIR, REF_DIR, LOG_DIR, EXPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)
