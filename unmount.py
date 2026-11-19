"""Eject the media in a drive on Windows."""

import ctypes
from ctypes import wintypes
from pathlib import Path

# from winioctl.h, via CTL_CODE(device, function, method, access)
FSCTL_LOCK_VOLUME = 0x00090018            # FILE_DEVICE_FILE_SYSTEM, 6, METHOD_BUFFERED, FILE_ANY_ACCESS
IOCTL_STORAGE_MEDIA_REMOVAL = 0x002D4804  # FILE_DEVICE_MASS_STORAGE, 0x201, METHOD_BUFFERED, FILE_READ_ACCESS
IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808    # FILE_DEVICE_MASS_STORAGE, 0x202, METHOD_BUFFERED, FILE_READ_ACCESS

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_kernel32 = ctypes.windll.kernel32
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
_kernel32.DeviceIoControl.restype = wintypes.BOOL


def _ioctl(handle, code, buffer=None, size=0) -> bool:
    returned = wintypes.DWORD()
    return bool(_kernel32.DeviceIoControl(handle, code, buffer, size, None, 0, ctypes.byref(returned), None))


def unmount_drive(drive: Path) -> None:
    """Eject the removable media in the drive. Raises OSError when the OS refuses (volume busy, fixed disk)."""
    # shortcut: media eject only — some readers keep the letter and fixed disks refuse; upgrade to CM_Request_Device_Eject if the letter must always disappear
    device_path = "\\\\.\\" + str(drive).rstrip("\\")
    # GENERIC_READ, FILE_SHARE_READ|FILE_SHARE_WRITE, OPEN_EXISTING
    handle = _kernel32.CreateFileW(device_path, 0x80000000, 0x3, None, 3, 0, None)
    if handle in (None, INVALID_HANDLE_VALUE):
        raise OSError(f"Cannot open {drive} for ejection: {ctypes.WinError()}")

    try:
        _ioctl(handle, FSCTL_LOCK_VOLUME)  # best-effort: fails while another program holds the volume
        allow_removal = ctypes.c_byte(0)  # PREVENT_MEDIA_REMOVAL = FALSE
        _ioctl(handle, IOCTL_STORAGE_MEDIA_REMOVAL, ctypes.byref(allow_removal), ctypes.sizeof(allow_removal))
        if not _ioctl(handle, IOCTL_STORAGE_EJECT_MEDIA):
            raise OSError(f"Eject failed for {drive}: {ctypes.WinError()}")
    finally:
        _kernel32.CloseHandle(handle)
