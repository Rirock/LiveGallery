from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtGui import QImage, QPixmap

from src.app_paths import APP_ROOT
from src.models.media_item import MediaItem

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover - optional runtime integration
    imageio_ffmpeg = None

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional runtime integration
    pillow_heif = None


LOGGER = logging.getLogger(__name__)


class ThumbnailService:
    PREVIEW_CACHE_SIZE = 32
    THUMB_PIXMAP_CACHE_SIZE = 256
    THUMBNAIL_BUCKET_STEP = 24
    MAX_DISK_CACHE_BYTES = 256 * 1024 * 1024
    TARGET_DISK_CACHE_BYTES = 192 * 1024 * 1024

    def __init__(self, thumbnail_dir: Path) -> None:
        self.thumbnail_dir = Path(thumbnail_dir)
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_path = self._resolve_ffmpeg_path()
        self._preview_cache: OrderedDict[tuple[str, int, int, int], QImage] = OrderedDict()
        self._thumb_pixmap_cache: OrderedDict[tuple[str, int], QPixmap] = OrderedDict()
        self._preview_cache_lock = threading.Lock()
        self._thumb_cache_lock = threading.Lock()
        self._disk_cleanup_started = False
        self.start_background_cleanup()

    def ensure_thumbnail(self, item: MediaItem, size: int) -> Path:
        output_path = self.cached_thumbnail_path(item, size)
        try:
            if output_path.exists() and output_path.stat().st_size > 0:
                self._touch_path(output_path)
                return output_path
        except OSError:
            pass

        output_path.parent.mkdir(parents=True, exist_ok=True)
        cache_size = self._bucket_thumbnail_size(size)
        if item.media_type == "video":
            created = self._generate_video_thumbnail(Path(item.path), output_path, cache_size)
        else:
            created = self._generate_image_thumbnail(Path(item.path), output_path, cache_size)

        if created:
            self._touch_path(output_path)
            self.prune_disk_cache()
            return output_path

        self._generate_placeholder(
            output_path,
            cache_size,
            "VIDEO" if item.media_type == "video" else "IMAGE",
        )
        return output_path

    def cached_thumbnail_path(self, item: MediaItem, size: int) -> Path:
        return self.thumbnail_dir / self._thumbnail_filename(item, size)

    def find_existing_thumbnail(self, item: MediaItem, size: int) -> Path | None:
        output_path = self.cached_thumbnail_path(item, size)
        try:
            if output_path.exists() and output_path.stat().st_size > 0:
                self._touch_path(output_path)
                return output_path
        except OSError:
            return None
        return None

    def load_preview_pixmap(self, image_path: str, max_edge: int = 2200) -> QPixmap:
        image = self.load_preview_image(image_path, max_edge=max_edge)
        if image.isNull():
            return QPixmap(image_path)
        return QPixmap.fromImage(image)

    def load_preview_image(self, image_path: str, max_edge: int = 2200) -> QImage:
        path = Path(image_path)
        try:
            stat_result = path.stat()
        except OSError:
            stat_result = None

        cache_key = None
        if stat_result is not None:
            cache_key = (str(path.resolve()), stat_result.st_mtime_ns, stat_result.st_size, max_edge)
            with self._preview_cache_lock:
                cached = self._preview_cache.get(cache_key)
                if cached is not None and not cached.isNull():
                    self._preview_cache.move_to_end(cache_key)
                    return cached.copy()

        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                image = image.convert("RGBA")
                image.thumbnail((max_edge, max_edge))
                qimage = QImage(
                    image.tobytes("raw", "RGBA"),
                    image.width,
                    image.height,
                    QImage.Format.Format_RGBA8888,
                ).copy()
                if cache_key is not None and not qimage.isNull():
                    with self._preview_cache_lock:
                        self._store_lru(self._preview_cache, cache_key, qimage, self.PREVIEW_CACHE_SIZE)
                return qimage
        except (OSError, UnidentifiedImageError) as exc:
            LOGGER.warning("Failed to load preview image %s: %s", image_path, exc)
            return QImage()

    def load_thumbnail_pixmap(self, thumb_path: str, size: int) -> QPixmap:
        cache_key = (thumb_path, size)
        with self._thumb_cache_lock:
            cached = self._thumb_pixmap_cache.get(cache_key)
            if cached is not None and not cached.isNull():
                self._thumb_pixmap_cache.move_to_end(cache_key)
                return cached

        pixmap = QPixmap(thumb_path)
        if not pixmap.isNull():
            with self._thumb_cache_lock:
                self._store_lru(self._thumb_pixmap_cache, cache_key, pixmap, self.THUMB_PIXMAP_CACHE_SIZE)
        return pixmap

    def _generate_image_thumbnail(self, source_path: Path, output_path: Path, size: int) -> bool:
        try:
            with Image.open(source_path) as image:
                image = ImageOps.exif_transpose(image)
                image = image.convert("RGB")
                contained = ImageOps.contain(image, (size, size))
                canvas = Image.new("RGB", (size, size), "#eef2f7")
                x = (size - contained.width) // 2
                y = (size - contained.height) // 2
                canvas.paste(contained, (x, y))
                canvas.save(output_path, format="JPEG", quality=88)
                return True
        except (OSError, UnidentifiedImageError) as exc:
            LOGGER.warning("Failed to create image thumbnail for %s: %s", source_path, exc)
            return False

    def _generate_video_thumbnail(self, source_path: Path, output_path: Path, size: int) -> bool:
        if not self.ffmpeg_path:
            LOGGER.warning("No ffmpeg executable available for video thumbnail generation")
            return False

        filter_value = (
            f"scale={size}:{size}:force_original_aspect_ratio=decrease,"
            f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=0xeef2f7"
        )
        try:
            completed = subprocess.run(
                [
                    self.ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    "0.2",
                    "-i",
                    str(source_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    filter_value,
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.warning("Failed to run ffmpeg for %s: %s", source_path, exc)
            return False
        return completed.returncode == 0 and output_path.exists()

    def _generate_placeholder(self, output_path: Path, size: int, label: str) -> None:
        image = Image.new("RGB", (size, size), "#d9e1ea")
        image.save(output_path, format="JPEG", quality=82)

    def _thumbnail_filename(self, item: MediaItem, size: int) -> str:
        cache_size = self._bucket_thumbnail_size(size)
        digest = hashlib.sha1(
            f"{item.path}|{item.file_mtime}|{item.file_size}|{cache_size}".encode("utf-8")
        ).hexdigest()
        return f"{digest}.jpg"

    @classmethod
    def _bucket_thumbnail_size(cls, size: int) -> int:
        if size <= 0:
            return cls.THUMBNAIL_BUCKET_STEP
        step = cls.THUMBNAIL_BUCKET_STEP
        return max(
            step,
            ((size + (step // 2)) // step) * step,
        )

    @staticmethod
    def _store_lru(cache: OrderedDict, key, value, max_size: int) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > max_size:
            cache.popitem(last=False)

    def start_background_cleanup(self) -> None:
        if self._disk_cleanup_started:
            return
        self._disk_cleanup_started = True
        cleanup_thread = threading.Thread(
            target=self.prune_disk_cache,
            name="thumbnail-cache-prune",
            daemon=True,
        )
        cleanup_thread.start()

    def prune_disk_cache(
        self,
        max_bytes: int | None = None,
        target_bytes: int | None = None,
    ) -> int:
        limit = max_bytes or self.MAX_DISK_CACHE_BYTES
        target = target_bytes or self.TARGET_DISK_CACHE_BYTES

        files: list[tuple[Path, int, float]] = []
        total_size = 0
        for file_path in self.thumbnail_dir.glob("*.jpg"):
            try:
                stat_result = file_path.stat()
            except OSError:
                continue
            if stat_result.st_size <= 0:
                continue
            files.append((file_path, stat_result.st_size, stat_result.st_mtime))
            total_size += stat_result.st_size

        if total_size <= limit:
            return 0

        removed_count = 0
        for file_path, file_size, _mtime in sorted(files, key=lambda entry: entry[2]):
            if total_size <= target:
                break
            try:
                file_path.unlink()
            except OSError:
                continue
            total_size -= file_size
            removed_count += 1

        if removed_count:
            LOGGER.info(
                "Pruned %s thumbnail cache files, remaining size %.2f MB",
                removed_count,
                total_size / (1024 ** 2),
            )
        return removed_count

    @staticmethod
    def _resolve_ffmpeg_path() -> str | None:
        bundled_candidates = [
            APP_ROOT / "ffmpeg.exe",
            *APP_ROOT.glob("*ffmpeg*.exe"),
            *(APP_ROOT / "_internal").glob("*ffmpeg*.exe"),
            *(APP_ROOT / "_internal" / "imageio_ffmpeg" / "binaries").glob("*ffmpeg*.exe"),
        ]
        for bundled_ffmpeg in bundled_candidates:
            if bundled_ffmpeg.exists():
                return str(bundled_ffmpeg)
        if imageio_ffmpeg is not None:
            try:
                return imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                pass
        return shutil.which("ffmpeg")

    @staticmethod
    def _touch_path(path: Path) -> None:
        try:
            os.utime(path, None)
        except OSError:
            return
