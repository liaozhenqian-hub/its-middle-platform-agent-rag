from __future__ import annotations

import re
import stat
from pathlib import Path, PurePosixPath
import zipfile


class UnsafeArchiveError(ValueError):
    pass


DEFAULT_DOCUMENT_SUFFIXES = frozenset({".md", ".txt", ".docx", ".pdf"})
_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:")


def extract_upload_archive(
    archive_path: str | Path,
    destination: str | Path,
    max_files: int,
    max_bytes: int,
    allowed_suffixes: set[str] | None = None,
    max_file_bytes: int | None = None,
) -> list[Path]:
    destination_path = Path(destination).resolve()
    suffixes = (
        DEFAULT_DOCUMENT_SUFFIXES
        if allowed_suffixes is None
        else frozenset(_normalize_suffix(value) for value in allowed_suffixes)
    )
    planned: list[tuple[zipfile.ZipInfo, Path]] = []
    seen_file_paths: dict[str, str] = {}
    total_bytes = 0
    with zipfile.ZipFile(archive_path) as archive:
        file_infos = [item for item in archive.infolist() if not item.is_dir()]
        if len(file_infos) > max_files:
            raise UnsafeArchiveError("archive contains too many files")
        for info in file_infos:
            normalized_name = info.filename.replace("\\", "/")
            member = PurePosixPath(normalized_name)
            mode = info.external_attr >> 16
            if member.is_absolute() or _WINDOWS_DRIVE_PATH.match(normalized_name):
                raise UnsafeArchiveError("archive absolute paths are not allowed")
            if ".." in member.parts:
                raise UnsafeArchiveError("archive path traversal is not allowed")
            if mode and stat.S_ISLNK(mode):
                raise UnsafeArchiveError("archive symbolic links are not allowed")
            canonical_path = member.as_posix()
            path_key = canonical_path.casefold()
            if path_key in seen_file_paths:
                raise UnsafeArchiveError(
                    "archive contains duplicate or case-insensitive paths"
                )
            parent_keys = {
                "/".join(member.parts[:index]).casefold()
                for index in range(1, len(member.parts))
            }
            if parent_keys.intersection(seen_file_paths):
                raise UnsafeArchiveError("archive contains a file/parent path conflict")
            child_prefix = f"{path_key}/"
            if any(existing.startswith(child_prefix) for existing in seen_file_paths):
                raise UnsafeArchiveError("archive contains a file/parent path conflict")
            seen_file_paths[path_key] = canonical_path
            total_bytes += info.file_size
            if max_file_bytes is not None and info.file_size > max_file_bytes:
                raise UnsafeArchiveError("archive single file is too large")
            if total_bytes > max_bytes:
                raise UnsafeArchiveError("archive is too large")
            suffix = Path(member.name).suffix.lower()
            if suffix not in suffixes:
                continue
            target = (destination_path / Path(*member.parts)).resolve()
            if destination_path not in target.parents:
                raise UnsafeArchiveError("archive path traversal is not allowed")
            planned.append((info, target))

        destination_path.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        for info, target in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                while block := source.read(1024 * 1024):
                    output.write(block)
            extracted.append(target)
    return extracted


def _normalize_suffix(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("allowed suffixes must not be empty")
    return normalized if normalized.startswith(".") else f".{normalized}"
