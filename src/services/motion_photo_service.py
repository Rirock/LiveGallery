from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class MotionPhotoInfo:
    is_motion_photo: bool
    extracted_video_path: str | None = None
    markers: tuple[str, ...] = ()


class MotionPhotoService:
    HEAD_SCAN_BYTES = 512 * 1024
    MAX_CACHE_BYTES = 1024 * 1024 * 1024
    TARGET_CACHE_BYTES = 768 * 1024 * 1024
    MARKERS = (
        "MotionPhoto",
        "MicroVideo",
        "GCamera",
        "Container:Directory",
        "Item:Mime=\"video/mp4\"",
        "Mime=\"video/mp4\"",
    )

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_lock = threading.RLock()
        self._cleanup_started = False
        self.start_background_cleanup()

    def inspect(self, image_path: Path, file_size: int, file_mtime: float) -> MotionPhotoInfo:
        detected_markers = self._read_markers(image_path)
        is_name_candidate = image_path.stem.upper().startswith("MVIMG")
        is_candidate = bool(detected_markers) or is_name_candidate
        if not is_candidate:
            return MotionPhotoInfo(is_motion_photo=False)

        return MotionPhotoInfo(
            is_motion_photo=True,
            extracted_video_path=None,
            markers=tuple(detected_markers),
        )

    def start_background_cleanup(self) -> None:
        if self._cleanup_started:
            return
        self._cleanup_started = True
        cleanup_thread = threading.Thread(
            target=self.prune_cache,
            name="motion-photo-cache-prune",
            daemon=True,
        )
        cleanup_thread.start()

    def cached_video_path(
        self,
        image_path: Path,
        file_size: int,
        file_mtime: float,
    ) -> Path:
        return self._cache_path_for(image_path, file_size, file_mtime)

    def existing_cached_video(
        self,
        image_path: Path,
        file_size: int,
        file_mtime: float,
    ) -> Path | None:
        output_path = self.cached_video_path(image_path, file_size, file_mtime)
        try:
            if output_path.exists() and output_path.stat().st_size > 0:
                output_path.touch()
                return output_path
        except OSError:
            return None
        return None

    def extract_embedded_video(
        self,
        image_path: Path,
        file_size: int,
        file_mtime: float,
    ) -> Path | None:
        output_path = self.cached_video_path(image_path, file_size, file_mtime)
        cached_path = self.existing_cached_video(image_path, file_size, file_mtime)
        if cached_path is not None:
            return cached_path

        try:
            data = image_path.read_bytes()
        except OSError as exc:
            LOGGER.warning("Failed to read motion photo candidate %s: %s", image_path, exc)
            return None

        with self._cache_lock:
            try:
                if output_path.exists() and output_path.stat().st_size > 0:
                    output_path.touch()
                    return output_path
            except OSError:
                pass

            for start in self._candidate_mp4_starts(data):
                chunk = data[start:]
                if self._looks_like_mp4(chunk):
                    try:
                        output_path.write_bytes(chunk)
                        output_path.touch()
                        self.prune_cache()
                        return output_path
                    except OSError as exc:
                        LOGGER.warning("Failed to cache embedded video for %s: %s", image_path, exc)
                        return None
        return None

    def prune_cache(
        self,
        max_bytes: int | None = None,
        target_bytes: int | None = None,
    ) -> int:
        limit = max_bytes or self.MAX_CACHE_BYTES
        target = target_bytes or self.TARGET_CACHE_BYTES

        with self._cache_lock:
            files: list[tuple[Path, int, float]] = []
            total_size = 0
            for file_path in self.cache_dir.glob("*.mp4"):
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
                    "Pruned %s motion photo cache files, remaining size %.2f GB",
                    removed_count,
                    total_size / (1024 ** 3),
                )
            return removed_count

    def _read_markers(self, image_path: Path) -> list[str]:
        try:
            with image_path.open("rb") as handle:
                head = handle.read(self.HEAD_SCAN_BYTES)
        except OSError as exc:
            LOGGER.warning("Failed to read XMP header from %s: %s", image_path, exc)
            return []

        text = head.decode("utf-8", errors="ignore")
        return [marker for marker in self.MARKERS if marker in text]

    def _candidate_mp4_starts(self, data: bytes) -> list[int]:
        candidates: list[int] = []
        text = data[: self.HEAD_SCAN_BYTES].decode("utf-8", errors="ignore")

        for pattern in (
            r"MicroVideoOffset[=\"\s:>]+(\d+)",
            r"Item:Length[=\"\s:>]+(\d+)",
        ):
            match = re.search(pattern, text)
            if match:
                offset = int(match.group(1))
                candidate = len(data) - offset
                if 0 <= candidate < len(data):
                    if candidate >= 4 and data[candidate + 4 : candidate + 8] == b"ftyp":
                        candidates.append(candidate)
                    candidates.append(candidate)

        search_start = max(0, len(data) - 32 * 1024 * 1024)
        position = search_start
        while True:
            index = data.find(b"ftyp", position)
            if index < 0:
                break
            if index >= 4:
                box_start = index - 4
                box_size = int.from_bytes(data[box_start:index], byteorder="big", signed=False)
                if 8 <= box_size <= 128:
                    candidates.append(box_start)
            candidates.append(index)
            position = index + 4

        unique_candidates: list[int] = []
        seen: set[int] = set()
        for value in candidates:
            if value not in seen and 0 <= value < len(data):
                seen.add(value)
                unique_candidates.append(value)
        return unique_candidates

    @staticmethod
    def _looks_like_mp4(data: bytes) -> bool:
        if len(data) < 64:
            return False
        if data[4:8] != b"ftyp":
            return False
        return b"mdat" in data[: 4 * 1024 * 1024] or b"moov" in data[: 4 * 1024 * 1024]

    def _cache_path_for(self, image_path: Path, file_size: int, file_mtime: float) -> Path:
        mtime_token = int(file_mtime * 1_000_000)
        digest = hashlib.sha1(
            f"{image_path.resolve()}|{file_size}|{mtime_token}".encode("utf-8")
        ).hexdigest()
        return self.cache_dir / f"{digest}.mp4"
