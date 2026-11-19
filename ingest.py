"""Copy selected files safely from a camera card to a destination folder."""

import os
import shutil
from pathlib import Path
from typing import Callable


ProgressCallback = Callable[[int, int, str, str], None]


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
) -> tuple[int, int, int]:
    """Copy without overwriting. Callback: (completed, total, filename, status)."""
    destination.mkdir(parents=True, exist_ok=True)
    copied = skipped = errors = 0
    total = len(files)

    for index, source in enumerate(files, start=1):
        destination_file = destination / source.name
        created_destination = False
        status = "ERROR"

        try:
            with source.open("rb") as source_stream:
                with destination_file.open("xb") as destination_stream:
                    created_destination = True
                    shutil.copyfileobj(source_stream, destination_stream)

            if source.stat().st_size != destination_file.stat().st_size:
                destination_file.unlink(missing_ok=True)
                errors += 1
                status = "VERIFY ERROR"
            else:
                copied += 1
                status = "COPIED"

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
            progress_callback(index, total, source.name, status)

    return copied, skipped, errors
