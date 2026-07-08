from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from src.app_paths import DATABASE_PATH, LOGO_PATH, LOG_DIR, MOTION_CACHE_DIR, THUMBNAIL_DIR, ensure_app_dirs
from src.services.database_service import DatabaseService
from src.services.media_scanner import MediaScanner
from src.services.metadata_service import MetadataService
from src.services.motion_photo_service import MotionPhotoService
from src.services.thumbnail_service import ThumbnailService
from src.views.main_window import MainWindow


def configure_logging() -> None:
    log_file = LOG_DIR / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    ensure_app_dirs()
    configure_logging()

    database_service = DatabaseService(DATABASE_PATH)
    database_service.initialize()

    motion_photo_service = MotionPhotoService(MOTION_CACHE_DIR)
    metadata_service = MetadataService(motion_photo_service)
    thumbnail_service = ThumbnailService(THUMBNAIL_DIR)
    scanner = MediaScanner(database_service, metadata_service)

    app = QApplication(sys.argv)
    app.setApplicationName("LiveGallery")
    app.setOrganizationName("Rirock")
    if LOGO_PATH.exists():
        app.setWindowIcon(QIcon(str(LOGO_PATH)))

    window = MainWindow(database_service, scanner, thumbnail_service)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
