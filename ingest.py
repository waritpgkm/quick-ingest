"""Copy selected files safely from a camera card to a destination folder."""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Callable


ProgressCallback = Callable[[int, int, str, str], None]

MANIFEST_FILE = ".quickingest.json"
INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_prefix(prefix: str) -> str:
    """Strip characters Windows forbids in file names."""
    return INVALID_NAME_CHARS.sub("", prefix).strip().rstrip(".")


def next_sequence_number(destination: Path, prefix: str) -> int:
    """Return the first unused number for PREFIX_n in the destination (padded or not)."""
    highest = 0
    # shortcut: unreadable destination restarts at 1, collisions then show up as SKIPPED
    try:
        existing_names = os.listdir(destination) if destination.is_dir() else []
    except OSError:
        existing_names = []
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)\.", re.IGNORECASE)
    for existing in existing_names:
        match = pattern.match(existing)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def assign_sequence_names(files: list[Path], destination: Path, prefix: str, digits: int = 1) -> list[str]:
    """Map files to zero-padded PREFIX_n.ext: ARW/JPG of one photo share n, n continues past existing files."""
    groups: dict[Path, list[Path]] = {}
    for source in files:
        groups.setdefault(source.with_suffix(""), []).append(source)

    assigned: dict[Path, str] = {}
    number = next_sequence_number(destination, prefix)
    for group in groups.values():
        for source in group:
            assigned[source] = f"{prefix}_{number:0{digits}d}{source.suffix}"
        number += 1
    return [assigned[source] for source in files]


def load_manifest(destination: Path) -> dict[str, str]:
    """Return the recorded source-filename to destination-name map; corrupt or absent history reads as empty."""
    try:
        entries = json.loads((destination / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(entries, dict):
        return {}
    # older builds keyed entries as "name|size"; filenames cannot contain "|" on Windows
    return {key.rsplit("|", 1)[0]: value for key, value in entries.items()}


def save_manifest(destination: Path, entries: dict[str, str]) -> None:
    try:
        (destination / MANIFEST_FILE).write_text(json.dumps(entries), encoding="utf-8")
    except OSError:
        pass  # best-effort: dedup falls back to filename matching


def new_photos(files: list[Path], destination: Path) -> list[Path]:
    """Drop files the destination already has, by filename alone."""
    # shortcut: identity = filename only (sizes change when photos are cropped), upgrade to a content hash if same-named files across folders must be told apart
    try:
        dest_names = {item.name for item in destination.iterdir()}
    except OSError:
        dest_names = set()
    manifest = load_manifest(destination)

    kept = []
    for source in files:
        if source.name in dest_names:
            continue  # original filename already in the destination
        target = manifest.get(source.name)
        if target is not None and target in dest_names:
            continue  # renamed copy still present
        kept.append(source)
    return kept


def is_destination_inside_card(card_root: Path, destination: Path) -> bool:
    """Return True if destination is the card root or is located inside it."""
    card_root = card_root.resolve()
    destination = destination.resolve()
    try:
        return os.path.commonpath([str(card_root), str(destination)]) == str(card_root)
    except ValueError:
        return False


def copy_files(
    files: list[Path],
    destination: Path,
    progress_callback: ProgressCallback | None = None,
    dest_names: list[str] | None = None,
) -> tuple[int, int, int]:
    """Copy without overwriting. Callback: (completed, total, filename, status)."""
    destination.mkdir(parents=True, exist_ok=True)
    names = dest_names if dest_names is not None else [source.name for source in files]
    copied = skipped = errors = 0
    total = len(files)
    copied_entries: dict[str, str] = {}

    for index, (source, name) in enumerate(zip(files, names, strict=True), start=1):
        destination_file = destination / name
        created_destination = False
        status = "ERROR"

        try:
            with source.open("rb") as source_stream:
                with destination_file.open("xb") as destination_stream:
                    created_destination = True
                    shutil.copyfileobj(source_stream, destination_stream)

            source_size = source.stat().st_size
            if source_size != destination_file.stat().st_size:
                destination_file.unlink(missing_ok=True)
                errors += 1
                status = "VERIFY ERROR"
            else:
                copied += 1
                status = "COPIED"
                copied_entries[source.name] = name

        except FileExistsError:
            skipped += 1
            status = "SKIPPED"
        except (OSError, shutil.Error):
            if created_destination:
                try:
                    destination_file.unlink(missing_ok=True)
                except OSError:
                    pass
            errors += 1
            status = "ERROR"

        if progress_callback:
            progress_callback(index, total, name, status)

    if copied_entries:
        entries = load_manifest(destination)
        entries.update(copied_entries)
        save_manifest(destination, entries)

    return copied, skipped, errors
