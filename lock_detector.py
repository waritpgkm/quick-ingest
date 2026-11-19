"""Find image files and optionally filter them by the Windows read-only attribute."""

import ctypes
from pathlib import Path

FILE_ATTRIBUTE_READONLY = 0x00000001
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

SUPPORTED_EXTENSIONS = {".arw", ".jpg", ".jpeg"}


def is_protected(file_path: Path) -> bool:
    """
    Check the Windows read-only attribute.

    Important: this is the protection proxy used by the current QuickIngest
    implementation. Verify it against files protected in-camera on the actual
    Sony card before relying on it for important ingest sessions.
    """
    attributes = ctypes.windll.kernel32.GetFileAttributesW(str(file_path))

    if attributes == INVALID_FILE_ATTRIBUTES:
        return False

    return bool(attributes & FILE_ATTRIBUTE_READONLY)


def scan_images(
    dcim_path: Path,
    extensions: list[str] | set[str] | None = None,
    protected_only: bool = True,
) -> list[Path]:
    """Recursively find matching image files under DCIM."""
    if extensions is None:
        extensions = SUPPORTED_EXTENSIONS

    normalized_extensions = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in extensions
    }

    found_files = []

    try:
        for file_path in dcim_path.rglob("*"):
            try:
                if not file_path.is_file():
                    continue

                if file_path.suffix.lower() not in normalized_extensions:
                    continue

                if protected_only and not is_protected(file_path):
                    continue

                found_files.append(file_path)

            except (OSError, PermissionError):
                # Skip files that become inaccessible during scanning.
                continue

    except (OSError, PermissionError):
        raise

    return sorted(found_files, key=lambda path: str(path).lower())
