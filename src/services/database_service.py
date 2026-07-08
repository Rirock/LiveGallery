from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from src.models.media_item import MediaItem


class DatabaseService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    folder TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    capture_time TEXT NOT NULL,
                    file_mtime REAL NOT NULL,
                    file_size INTEGER NOT NULL,
                    width INTEGER,
                    height INTEGER,
                    is_favorite INTEGER NOT NULL DEFAULT 0,
                    is_motion_photo INTEGER NOT NULL DEFAULT 0,
                    motion_video_path TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_media_folder ON media(folder);
                CREATE INDEX IF NOT EXISTS idx_media_capture_time ON media(capture_time DESC);
                CREATE INDEX IF NOT EXISTS idx_media_filename ON media(filename COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_media_type ON media(media_type);
                CREATE INDEX IF NOT EXISTS idx_media_favorite ON media(is_favorite);
                CREATE INDEX IF NOT EXISTS idx_media_motion ON media(is_motion_photo);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        # This database is an index/cache only, so MEMORY journaling keeps it stable
        # on removable or sync-backed folders where SQLite file locking is fragile.
        connection.execute("PRAGMA journal_mode=MEMORY;")
        connection.execute("PRAGMA synchronous=OFF;")
        connection.execute("PRAGMA temp_store=MEMORY;")
        return connection

    def load_existing_for_folder(
        self,
        folder: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, sqlite3.Row]:
        owns_connection = connection is None
        connection = connection or self.connect()
        try:
            folder_like = self._path_like_pattern(folder)
            rows = connection.execute(
                """
                SELECT path, file_mtime, file_size, is_favorite, is_motion_photo, motion_video_path
                FROM media
                WHERE path LIKE ? COLLATE NOCASE
                """,
                (folder_like,),
            ).fetchall()
            return {row["path"]: row for row in rows}
        finally:
            if owns_connection:
                connection.close()

    def upsert_media(self, connection: sqlite3.Connection, item: MediaItem) -> None:
        connection.execute(
            """
            INSERT INTO media (
                path,
                folder,
                filename,
                media_type,
                extension,
                capture_time,
                file_mtime,
                file_size,
                width,
                height,
                is_favorite,
                is_motion_photo,
                motion_video_path,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(path) DO UPDATE SET
                folder = excluded.folder,
                filename = excluded.filename,
                media_type = excluded.media_type,
                extension = excluded.extension,
                capture_time = excluded.capture_time,
                file_mtime = excluded.file_mtime,
                file_size = excluded.file_size,
                width = excluded.width,
                height = excluded.height,
                is_motion_photo = excluded.is_motion_photo,
                motion_video_path = excluded.motion_video_path,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                item.path,
                item.folder,
                item.filename,
                item.media_type,
                item.extension,
                item.capture_time_db,
                item.file_mtime,
                item.file_size,
                item.width,
                item.height,
                int(item.is_favorite),
                int(item.is_motion_photo),
                item.motion_video_path,
            ),
        )

    def remove_missing_for_folder(
        self,
        connection: sqlite3.Connection,
        folder: str,
        existing_paths: Iterable[str],
    ) -> int:
        folder_like = self._path_like_pattern(folder)
        known_paths = {
            row["path"]
            for row in connection.execute(
                "SELECT path FROM media WHERE path LIKE ? COLLATE NOCASE",
                (folder_like,),
            ).fetchall()
        }
        missing_paths = known_paths.difference(existing_paths)
        if not missing_paths:
            return 0
        connection.executemany(
            "DELETE FROM media WHERE path = ?",
            ((path,) for path in missing_paths),
        )
        return len(missing_paths)

    def query_media(
        self,
        filter_mode: str = "all",
        search_text: str = "",
        root_folder: str | None = None,
    ) -> list[MediaItem]:
        clauses = []
        params: list[object] = []
        if filter_mode == "motion":
            clauses.append("is_motion_photo = 1")
        elif filter_mode == "videos":
            clauses.append("media_type = 'video'")
        elif filter_mode == "favorites":
            clauses.append("is_favorite = 1")

        if search_text:
            clauses.append("filename LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{self._escape_like(search_text)}%")

        if root_folder:
            clauses.append("path LIKE ? COLLATE NOCASE")
            params.append(self._path_like_pattern(root_folder))

        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM media
                {where_sql}
                ORDER BY capture_time DESC, filename COLLATE NOCASE DESC
                """,
                params,
            ).fetchall()
        return [MediaItem.from_row(row) for row in rows]

    def set_favorite(self, media_path: str, value: bool) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE media SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                (int(value), media_path),
            )
            connection.commit()

    def delete_media_for_folder(self, folder: str) -> int:
        folder_like = self._path_like_pattern(folder)
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM media WHERE path LIKE ? COLLATE NOCASE",
                (folder_like,),
            )
            connection.commit()
            return cursor.rowcount

    def get_setting(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            connection.commit()

    def get_stats(self) -> dict[str, int]:
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) AS c FROM media").fetchone()["c"]
            motion = connection.execute(
                "SELECT COUNT(*) AS c FROM media WHERE is_motion_photo = 1"
            ).fetchone()["c"]
            videos = connection.execute(
                "SELECT COUNT(*) AS c FROM media WHERE media_type = 'video'"
            ).fetchone()["c"]
            favorites = connection.execute(
                "SELECT COUNT(*) AS c FROM media WHERE is_favorite = 1"
            ).fetchone()["c"]
        return {
            "total": total,
            "motion": motion,
            "videos": videos,
            "favorites": favorites,
        }

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _is_under_root(media_path: str, root_folder: str) -> bool:
        normalized_media_path = str(Path(media_path)).replace("/", "\\").lower()
        normalized_root = str(Path(root_folder)).replace("/", "\\").lower().rstrip("\\")
        return normalized_media_path.startswith(normalized_root + "\\")

    @classmethod
    def _path_like_pattern(cls, root_folder: str) -> str:
        normalized_root = str(Path(root_folder)).replace("/", "\\").rstrip("\\")
        return f"{normalized_root}\\%"
