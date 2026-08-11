# dotpad_api.py
#
# Python ctypes wrapper for DotPad Windows SDK v3.
#
# Based on DotSDKAPI.h.
#
# This module has no NVDA-specific imports. It can be imported from an NVDA
# global plugin or from a standalone Python program.
#
# Important:
# - DotPadSDK.dll must match the architecture of the Python/NVDA process.
# - SDK functions use cdecl.
# - SDK callbacks use Windows CALLBACK (__stdcall).
# - Keep the DotPadSdkClient instance alive while the SDK may invoke callbacks.

from __future__ import annotations

import ctypes
import enum
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeAlias
from logHandler import log

DeviceHandle: TypeAlias = int
DispatchFunction: TypeAlias = Callable[[Callable[..., None], Any], None]
LogFunction: TypeAlias = Callable[[str], None]


class DotPadError(RuntimeError):
    """Base exception for DotPad SDK wrapper errors."""


class DotPadLoadError(DotPadError):
    """Raised when DotPadSDK.dll cannot be loaded."""


class DotPadDisposedError(DotPadError):
    """Raised when a closed DotPadSdkClient is used."""


class DotDataCode(enum.IntEnum):
    CONNECTED = 0
    DISCONNECTED = 1
    BOARD_INFO = 2
    BLE_MAC_ADDRESS = 3
    DEVICE_NAME = 4
    DEVICE_FW_VERSION = 5
    DEVICE_HW_VERSION = 6
    RESPONSE_DISPLAY_LINE_ACK = 7
    RESPONSE_DISPLAY_LINE_NON_ACK = 8
    RESPONSE_DISPLAY_LINE_COMPLETE = 9
    COMMAND_ERROR = 10
    COMMAND_NONE = 11


class DotKeyCode(enum.IntEnum):
    FUNCTION1 = 0
    FUNCTION2 = 1
    FUNCTION3 = 2
    FUNCTION4 = 3
    FUNCTION12 = 4
    FUNCTION13 = 5
    FUNCTION14 = 6
    FUNCTION23 = 7
    FUNCTION24 = 8
    FUNCTION34 = 9
    ELSE = 10
    PANNING_ALL = 11
    PANNING_LEFT = 12
    PANNING_RIGHT = 13
    LPF1 = 14
    RPF4 = 15


class DotPadLanguage(enum.IntEnum):
    ARABIC = 1
    CHINESE_TRADITIONAL = 2
    CHINESE_SIMPLIFIED = 3
    DUTCH = 4
    ENGLISH = 5
    FRENCH = 6
    GERMAN = 7
    ITALIAN = 8
    JAPANESE = 9
    KOREAN = 10
    RUSSIAN = 11
    SPANISH = 12
    VIETNAMESE = 13
    BULGARIAN = 14
    PORTUGUESE = 15
    CZECH = 16
    POLISH = 17
    NORWEGIAN = 18


class DotPadDisplayInfo:
    """Display dimensions and Braille-display availability."""

    __slots__ = ("width", "height", "has_braille")

    def __init__(self, width: int, height: int, has_braille: bool) -> None:
        self.width = width
        self.height = height
        self.has_braille = has_braille

    def __repr__(self) -> str:
        return (
            f"DotPadDisplayInfo(width={self.width}, height={self.height}, "
            f"has_braille={self.has_braille})"
        )


# SDK functions use cdecl, but callbacks use Windows CALLBACK (__stdcall).
_CALLBACK = ctypes.WINFUNCTYPE

ScanCallback = _CALLBACK(None, ctypes.c_wchar_p)

KeyCallback = ctypes.WINFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.c_int,
        ctypes.c_void_p,      # const char* message
)

MessageCallback = _CALLBACK(
    None,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_char_p,
)

DisplayCallback = _CALLBACK(
    None,
    ctypes.c_void_p,
)

BrailleTranslateCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
)

def _handle_value(handle: int | ctypes.c_void_p | None) -> int:
    if isinstance(handle, ctypes.c_void_p):
        return int(handle.value or 0)
    return int(handle or 0)


def _as_void_p(handle: int | ctypes.c_void_p | None) -> ctypes.c_void_p:
    return ctypes.c_void_p(_handle_value(handle))


def _decode_message(raw: bytes | None) -> str:
    if not raw:
        return ""

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("mbcs", errors="replace")
        except LookupError:
            return raw.decode("latin-1", errors="replace")


def _safe_enum(enum_type: type[enum.IntEnum], value: int) -> enum.IntEnum | int:
    try:
        return enum_type(value)
    except ValueError:
        return value


class DotPadNative:
    """Low-level ctypes binding for DotPadSDK.dll."""

    DEFAULT_DLL_NAME = "DotPadSDK.dll"

    def __init__(self, dll_path: str | os.PathLike[str]) -> None:
        path = Path(dll_path).expanduser().resolve()
        if not path.is_file():
            raise DotPadLoadError(f"DotPad SDK DLL was not found: {path}")

        self.path = path
        self._dll_directory = None

        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(path.parent))

        try:
            # The header's dynamic-loading typedefs use ordinary function
            # pointers, so exported SDK functions use cdecl.
            self.dll = ctypes.CDLL(str(path))
        except OSError as error:
            self.close()
            raise DotPadLoadError(
                f"Could not load {path.name}. Check the DLL architecture "
                "and dependent DLLs."
            ) from error

        self._declare_functions()

    def close(self) -> None:
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None

    def _declare_functions(self) -> None:
        # Connection management.
        self.connect_ble = self.dll.DOT_PAD_CONNECT_BLE
        self.connect_ble.argtypes = [ctypes.c_wchar_p]
        self.connect_ble.restype = ctypes.c_void_p

        self.connect_serial = self.dll.DOT_PAD_CONNECT_SERIAL
        self.connect_serial.argtypes = [ctypes.c_wchar_p]
        self.connect_serial.restype = ctypes.c_void_p

        self.disconnect = self.dll.DOT_PAD_DISCONNECT
        self.disconnect.argtypes = [ctypes.c_void_p]
        self.disconnect.restype = ctypes.c_bool

        self.get_connected_device_count = (
            self.dll.DOT_PAD_GET_CONNECTED_DEVICE_COUNT
        )
        self.get_connected_device_count.argtypes = []
        self.get_connected_device_count.restype = ctypes.c_int

        self.get_connected_device_handle = (
            self.dll.DOT_PAD_GET_CONNECTED_DEVICE_HANDLE
        )
        self.get_connected_device_handle.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.get_connected_device_handle.restype = ctypes.c_bool

        self.get_device_name = self.dll.DOT_PAD_GET_DEVICE_NAME
        self.get_device_name.argtypes = [ctypes.c_void_p]
        self.get_device_name.restype = ctypes.c_bool

        self.get_fw_version = self.dll.DOT_PAD_GET_FW_VERSION
        self.get_fw_version.argtypes = [ctypes.c_void_p]
        self.get_fw_version.restype = ctypes.c_bool

        self.get_hw_version = self.dll.DOT_PAD_GET_HW_VERSION
        self.get_hw_version.argtypes = [ctypes.c_void_p]
        self.get_hw_version.restype = ctypes.c_bool

        # Scanning.
        self.ble_scan = self.dll.DOT_PAD_BLE_SCAN
        self.ble_scan.argtypes = [ScanCallback]
        self.ble_scan.restype = None

        self.ble_scan_stop = self.dll.DOT_PAD_BLE_SCAN_STOP
        self.ble_scan_stop.argtypes = []
        self.ble_scan_stop.restype = None

        self.usb_scan = self.dll.DOT_PAD_USB_SCAN
        self.usb_scan.argtypes = [ScanCallback]
        self.usb_scan.restype = None

        # Graphic display.
        self.display_file = self.dll.DOT_PAD_DISPLAY_FILE
        self.display_file.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        self.display_file.restype = ctypes.c_bool

        self.display_data = self.dll.DOT_PAD_DISPLAY_DATA
        self.display_data.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        self.display_data.restype = ctypes.c_bool

        self.reset_display = self.dll.DOT_PAD_RESET_DISPLAY
        self.reset_display.argtypes = [ctypes.c_void_p]
        self.reset_display.restype = ctypes.c_bool

        # Braille/text display.
        self.braille_display = self.dll.DOT_PAD_BRAILLE_DISPLAY
        self.braille_display.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            BrailleTranslateCallback,
        ]
        self.braille_display.restype = ctypes.c_bool

        self.braille_display_data = self.dll.DOT_PAD_BRAILLE_DISPLAY_DATA
        self.braille_display_data.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        self.braille_display_data.restype = ctypes.c_bool

        self.braille_ascii_display = self.dll.DOT_PAD_BRAILLE_ASCII_DISPLAY
        self.braille_ascii_display.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
        ]
        self.braille_ascii_display.restype = ctypes.c_bool

        self.reset_braille_display = self.dll.DOT_PAD_RESET_BRAILLE_DISPLAY
        self.reset_braille_display.argtypes = [ctypes.c_void_p]
        self.reset_braille_display.restype = ctypes.c_bool

        # Settings.
        self.set_language = self.dll.DOT_PAD_SET_LANGUAGE
        self.set_language.argtypes = [ctypes.c_int, ctypes.c_int]
        self.set_language.restype = None

        self.set_english_grade_if_korean = (
            self.dll.DOT_PAD_SET_ENGLISH_GRADE_IF_KOREAN
        )
        self.set_english_grade_if_korean.argtypes = [ctypes.c_int]
        self.set_english_grade_if_korean.restype = None

        # Display information.
        self.get_display_info = self.dll.DOT_PAD_GET_DISPLAY_INFO
        self.get_display_info.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        self.get_display_info.restype = ctypes.c_bool

        # Listener registration.
        self.register_key_callback = self.dll.DOT_PAD_REGISTER_KEY_CALLBACK
        self.register_key_callback.argtypes = [KeyCallback]
        self.register_key_callback.restype = None

        self.register_message_callback = (
            self.dll.DOT_PAD_REGISTER_MESSAGE_CALLBACK
        )
        self.register_message_callback.argtypes = [MessageCallback]
        self.register_message_callback.restype = None

        self.register_display_callback = (
            self.dll.DOT_PAD_REGISTER_DISPLAY_CALLBACK
        )
        self.register_display_callback.argtypes = [DisplayCallback]
        self.register_display_callback.restype = None


