from __future__ import annotations

import logging
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from src.models.media_item import MediaItem
from src.services.motion_photo_service import MotionPhotoService

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional runtime integration
    pillow_heif = None


LOGGER = logging.getLogger(__name__)


class MetadataService:
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
    VIDEO_EXTENSIONS = {".mp4", ".mov"}
    EXIF_DATETIME_TAGS = (36867, 36868, 306)
    FILENAME_PATTERNS = (
        re.compile(r"(?:IMG|VID|MVIMG)?[_-]?(\d{8})[_-]?(\d{6})(?!\d)", re.IGNORECASE),
        re.compile(r"(\d{8})[_-](\d{6})(?!\d)"),
        re.compile(r"(\d{14})(?!\d)"),
    )

    def __init__(self, motion_photo_service: MotionPhotoService) -> None:
        self.motion_photo_service = motion_photo_service
        self.ffprobe_path = shutil.which("ffprobe")

    def build_media_item(
        self,
        media_path: Path,
        stat_result,
        existing_favorite: bool = False,
    ) -> MediaItem:
        extension = media_path.suffix.lower()
        media_type = self._detect_media_type(extension)
        width: int | None = None
        height: int | None = None
        capture_time: datetime | None = None
        is_motion_photo = False
        motion_video_path: str | None = None

        if media_type == "image":
            width, height, capture_time = self._read_image_metadata(media_path)
            if extension in {".jpg", ".jpeg"}:
                motion_info = self.motion_photo_service.inspect(
                    media_path,
                    stat_result.st_size,
                    stat_result.st_mtime,
                )
                is_motion_photo = motion_info.is_motion_photo
                motion_video_path = motion_info.extracted_video_path
        else:
            capture_time = self._parse_datetime_from_filename(media_path.stem) or self._read_video_capture_time(
                media_path
            )

        capture_time = (
            capture_time
            or self._parse_datetime_from_filename(media_path.stem)
            or datetime.fromtimestamp(stat_result.st_mtime)
        )

        return MediaItem(
            path=str(media_path.resolve()),
            folder=str(media_path.resolve().parent),
            filename=media_path.name,
            media_type=media_type,
            extension=extension,
            capture_time=capture_time,
            file_mtime=stat_result.st_mtime,
            file_size=stat_result.st_size,
            width=width,
            height=height,
            is_favorite=existing_favorite,
            is_motion_photo=is_motion_photo,
            motion_video_path=motion_video_path,
        )

    def _detect_media_type(self, extension: str) -> str:
        if extension in self.IMAGE_EXTENSIONS:
            return "image"
        if extension in self.VIDEO_EXTENSIONS:
            return "video"
        raise ValueError(f"Unsupported extension: {extension}")

    def _read_image_metadata(self, image_path: Path) -> tuple[int | None, int | None, datetime | None]:
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                exif = image.getexif()
                capture_time = self._parse_exif_datetime(exif)
                return width, height, capture_time
        except (OSError, UnidentifiedImageError) as exc:
            LOGGER.warning("Failed to read image metadata from %s: %s", image_path, exc)
        return None, None, None

    def _parse_exif_datetime(self, exif) -> datetime | None:
        if not exif:
            return None
        for tag_id in self.EXIF_DATETIME_TAGS:
            raw_value = exif.get(tag_id)
            parsed = self._parse_exif_datetime_value(raw_value)
            if parsed:
                return parsed
        return None

    @staticmethod
    def _parse_exif_datetime_value(raw_value) -> datetime | None:
        if not raw_value:
            return None
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8", errors="ignore")
        raw_value = str(raw_value).strip().replace("\x00", "")
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw_value, fmt)
            except ValueError:
                continue
        return None

    def _parse_datetime_from_filename(self, stem: str) -> datetime | None:
        for pattern in self.FILENAME_PATTERNS:
            match = pattern.search(stem)
            if not match:
                continue
            if len(match.groups()) == 1:
                value = match.group(1)
                if len(value) == 14:
                    try:
                        return datetime.strptime(value, "%Y%m%d%H%M%S")
                    except ValueError:
                        continue
            else:
                date_value, time_value = match.groups()
                try:
                    return datetime.strptime(date_value + time_value, "%Y%m%d%H%M%S")
                except ValueError:
                    continue

        if stem.isdigit() and len(stem) in {13, 10}:
            try:
                timestamp = int(stem)
                if len(stem) == 13:
                    timestamp = timestamp / 1000
                return datetime.fromtimestamp(timestamp)
            except (OSError, OverflowError, ValueError):
                return None

        return None

    def _read_video_capture_time(self, video_path: Path) -> datetime | None:
        if not self.ffprobe_path:
            return None
        try:
            completed = subprocess.run(
                [
                    self.ffprobe_path,
                    "-v",
                    "error",
                    "-show_entries",
                    "format_tags=creation_time:stream_tags=creation_time",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.warning("ffprobe failed for %s: %s", video_path, exc)
            return None

        if completed.returncode != 0:
            return None

        for line in completed.stdout.splitlines():
            value = line.strip()
            if not value:
                continue
            value = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(value)
                return parsed.replace(tzinfo=None)
            except ValueError:
                continue
        return None
