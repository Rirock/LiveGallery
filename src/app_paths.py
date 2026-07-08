from __future__ import annotations

from pathlib import Path
import sys


if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parents[1]

CACHE_DIR = APP_ROOT / "cache"
LOG_DIR = APP_ROOT / "logs"
THUMBNAIL_DIR = CACHE_DIR / "thumbnails"
MOTION_CACHE_DIR = CACHE_DIR / "motion_photos"
DATABASE_PATH = CACHE_DIR / "gallery.db"
LOGO_PATH = APP_ROOT / "logo.png"

DEFAULT_SCAN_DIR = APP_ROOT.parent / "Camera"


def ensure_app_dirs() -> None:
    for path in (CACHE_DIR, LOG_DIR, THUMBNAIL_DIR, MOTION_CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)
