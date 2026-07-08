from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from src.services.database_service import DatabaseService
from src.services.metadata_service import MetadataService


LOGGER = logging.getLogger(__name__)


ProgressCallback = Callable[[int, int, str], None]


class MediaScanner:
    SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".mp4", ".mov"}
    IGNORED_DIR_NAMES = {".temp", "cache", "__pycache__", ".git", ".venv"}
    COMMIT_INTERVAL = 100
    PROGRESS_INTERVAL = 25

    def __init__(self, database_service: DatabaseService, metadata_service: MetadataService) -> None:
        self.database_service = database_service
        self.metadata_service = metadata_service

    def scan_folder(
        self,
        folder_path: str | Path,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, int]:
        root_path = Path(folder_path).resolve()
        if not root_path.exists():
            raise FileNotFoundError(f"Folder not found: {root_path}")

        files = list(self._iter_media_files(root_path))
        total = len(files)
        summary = {"total": total, "added": 0, "updated": 0, "skipped": 0, "deleted": 0}

        with self.database_service.connect() as connection:
            existing = self.database_service.load_existing_for_folder(str(root_path), connection)
            seen_paths: set[str] = set()

            for index, media_path in enumerate(files, start=1):
                resolved_path = str(media_path.resolve())
                seen_paths.add(resolved_path)
                if progress_callback and (
                    index == 1 or index == total or index % self.PROGRESS_INTERVAL == 0
                ):
                    progress_callback(index, total, media_path.name)

                try:
                    stat_result = media_path.stat()
                except OSError as exc:
                    LOGGER.warning("Failed to stat %s: %s", media_path, exc)
                    continue

                existing_row = existing.get(resolved_path)
                if (
                    existing_row
                    and existing_row["file_size"] == stat_result.st_size
                    and abs(existing_row["file_mtime"] - stat_result.st_mtime) < 0.0001
                ):
                    summary["skipped"] += 1
                    continue

                item = self.metadata_service.build_media_item(
                    media_path,
                    stat_result,
                    existing_favorite=bool(existing_row["is_favorite"]) if existing_row else False,
                )
                self.database_service.upsert_media(connection, item)
                if existing_row:
                    summary["updated"] += 1
                else:
                    summary["added"] += 1

                if index % self.COMMIT_INTERVAL == 0:
                    connection.commit()

            summary["deleted"] = self.database_service.remove_missing_for_folder(
                connection,
                str(root_path),
                seen_paths,
            )
            connection.commit()

        return summary

    def _iter_media_files(self, root_path: Path):
        discovered_paths: list[Path] = []
        for current_root, dirs, files in os.walk(root_path, followlinks=False):
            dirs[:] = [
                name
                for name in dirs
                if not name.startswith(".") and name.lower() not in self.IGNORED_DIR_NAMES
            ]
            current_path = Path(current_root)
            for file_name in files:
                suffix = Path(file_name).suffix.lower()
                if suffix in self.SUPPORTED_EXTENSIONS:
                    discovered_paths.append(current_path / file_name)
        discovered_paths.sort(key=lambda item: str(item).lower())
        return discovered_paths
