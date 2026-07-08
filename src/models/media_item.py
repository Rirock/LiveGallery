from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Row


@dataclass(slots=True)
class MediaItem:
    path: str
    folder: str
    filename: str
    media_type: str
    extension: str
    capture_time: datetime
    file_mtime: float
    file_size: int
    width: int | None = None
    height: int | None = None
    is_favorite: bool = False
    is_motion_photo: bool = False
    motion_video_path: str | None = None
    id: int | None = None

    @property
    def capture_time_db(self) -> str:
        return self.capture_time.isoformat(sep=" ", timespec="seconds")

    @property
    def capture_time_display(self) -> str:
        return self.capture_time.strftime("%Y-%m-%d %H:%M:%S")

    @classmethod
    def from_row(cls, row: Row) -> "MediaItem":
        return cls(
            id=row["id"],
            path=row["path"],
            folder=row["folder"],
            filename=row["filename"],
            media_type=row["media_type"],
            extension=row["extension"],
            capture_time=datetime.fromisoformat(row["capture_time"]),
            file_mtime=row["file_mtime"],
            file_size=row["file_size"],
            width=row["width"],
            height=row["height"],
            is_favorite=bool(row["is_favorite"]),
            is_motion_photo=bool(row["is_motion_photo"]),
            motion_video_path=row["motion_video_path"],
        )
