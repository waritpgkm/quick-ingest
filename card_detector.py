"""Detect camera cards on Windows by locating a DCIM directory."""

import ctypes
from pathlib import Path
from typing import Optional


def get_connected_drives() -> list[Path]:
    """Return roots of currently mounted logical drives (Windows only)."""
    if __name__ != "__main__" and not hasattr(ctypes, "windll"):
        raise OSError("QuickIngest card detection currently supports Windows only.")

    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    drives = []

    for index in range(26):
        if bitmask & (1 << index):
            drives.append(Path(f"{chr(65 + index)}:\\"))
    return drives


def get_volume_name(drive: Path) -> str:
    """Return the Windows volume label, or an explanatory fallback."""
    volume_name = ctypes.create_unicode_buffer(261)
    filesystem_name = ctypes.create_unicode_buffer(261)

    success = ctypes.windll.kernel32.GetVolumeInformationW(
        str(drive),
        volume_name,
        len(volume_name),
        None,
        None,
        None,
        filesystem_name,
        len(filesystem_name),
    )

    if not success:
        return "(Unknown)"

    return volume_name.value or "(No label)"


def find_dcim_folder(drive: Path) -> Optional[Path]:
    """Return the DCIM directory if it exists and is accessible."""
    candidate = drive / "DCIM"

    try:
        if candidate.is_dir():
            return candidate
    except OSError:
        pass

    return None


def is_camera_card(drive: Path) -> bool:
    """A mounted drive is considered a camera card if it has an accessible DCIM folder."""
    return find_dcim_folder(drive) is not None


def find_camera_cards() -> list[dict]:
    """Find all currently connected drives containing an accessible DCIM folder."""
    cards = []

    for drive in get_connected_drives():
        dcim_path = find_dcim_folder(drive)
        if dcim_path is None:
            continue

        cards.append(
            {
                "drive": drive,
                "dcim": dcim_path,
                "volume_name": get_volume_name(drive),
            }
        )

    return cards
