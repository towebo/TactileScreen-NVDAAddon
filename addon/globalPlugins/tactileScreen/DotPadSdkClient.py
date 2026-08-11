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


from .dotPadSdk import (
	DotDataCode,
	DotKeyCode,
	DotPadLanguage,
	DotPadDisplayInfo,
	DotPadError,
	DotPadLoadError,
	DotPadNative,
	ScanCallback,
	KeyCallback,
	MessageCallback,
	DisplayCallback,
	BrailleTranslateCallback,
	_handle_value,
	_as_void_p,
	_decode_message,
	_safe_enum,
)

class DotPadSdkClient:
	"""
	High-level DotPad SDK client.

	Public callback attributes:
		on_ble_device_found(name)
		on_usb_port_found(port_name)
		on_key_pressed(
		device_handle, key_code, message
		)
		on_message_received(device_handle, message_code, message)
		on_display_completed(device_handle)
		on_braille_translated(device_handle, translated_data)

	The optional dispatch function is useful in NVDA:

		dispatch=lambda function, *args: queueHandler.queueFunction(
			queueHandler.eventQueue, function, *args
		)
	"""

	cellHeight: int = 4
	cellWidth: int = 2

	hCellCount: int 
	vCellCount: int
	bCellCount: int
	hPixelCount: int
	vPixelCount: int
	


	def __init__(
		self,
		dll_path: str | os.PathLike[str],
		*,
		log: LogFunction | None = None,
		dispatch: Callable[..., None] | None = None,
		) -> None:
		self._log_function = log
		self._dispatch_function = dispatch
		self._disposed = False
		self._lock = threading.RLock()
		self._display_buffers: dict[int, ctypes.Array[Any]] = {}

		self.hCellCount = 30
		self.vCellCount = 10
		self.bCellCount = 0
		self.hPixelCount = 60
		self.vPixelCount = 40
		self.resetDataBuffer()

		self.on_ble_device_found: Callable[[str], None] | None = None
		self.on_usb_port_found: Callable[[str], None] | None = None
		self.on_key_pressed: (
			Callable[[int, DotKeyCode | int, str], None] | None
			) = None
		self.on_message_received: (
			Callable[[int, DotDataCode | int, str], None] | None
			) = None
		self.on_display_completed: Callable[[int], None] | None = None
		self.on_braille_translated: Callable[[int, bytes], None] | None = None

		self._deviceBrailleData: dict[int, bytes] = {}
		self._deviceBrailleIndex: dict[int, int] = {}

		# Callback objects must remain strongly referenced.
		self._ble_scan_callback = ScanCallback(self._on_ble_scan)
		self._usb_scan_callback = ScanCallback(self._on_usb_scan)
		self._key_callback = KeyCallback(self._on_key_callback)
		self._message_callback = MessageCallback(self._on_message_callback)
		self._display_callback = DisplayCallback(self._on_display_callback)
		self._braille_callback = BrailleTranslateCallback(
			self._on_braille_translated_callback
			)

		self._native = DotPadNative(dll_path)
		self._native.register_key_callback(self._key_callback)
		self._native.register_message_callback(self._message_callback)
		self._native.register_display_callback(self._display_callback)

		self._log("DotPad callbacks registered")

	@classmethod
	def from_module_directory(
		cls,
		module_file: str,
		*,
		dll_name: str = DotPadNative.DEFAULT_DLL_NAME,
		log: LogFunction | None = None,
		dispatch: Callable[..., None] | None = None,
		) -> "DotPadSdkClient":
		"""Load DotPadSDK.dll from the directory containing module_file."""
		dll_path = Path(module_file).resolve().parent / dll_name
		return cls(dll_path, log=log, dispatch=dispatch)

	@property
	def disposed(self) -> bool:
		return self._disposed

	def _log(self, message: str) -> None:
		if self._log_function is None:
			return
		try:
			self._log_function(message)
		except Exception:
			pass

	def _throw_if_disposed(self) -> None:
		if self._disposed:
			raise DotPadDisposedError("DotPadSdkClient has been closed")

	def _dispatch(self, function: Callable[..., None] | None, *args: Any) -> None:
		if function is None or self._disposed:
			return

		try:
			if self._dispatch_function is None:
				function(*args)
			else:
				self._dispatch_function(function, *args)
		except Exception as error:
			self._log(f"DotPad event handler failed: {error!r}")

	# ------------------------------------------------------------------
	# Scanning and connection
	# ------------------------------------------------------------------

	def start_ble_scan(self) -> None:
		self._throw_if_disposed()
		self._native.ble_scan(self._ble_scan_callback)

	def stop_ble_scan(self) -> None:
		self._throw_if_disposed()
		self._native.ble_scan_stop()

	def start_usb_scan(self) -> None:
		self._throw_if_disposed()
		self._native.usb_scan(self._usb_scan_callback)

	def connect_ble(self, device_name: str) -> int:
		self._throw_if_disposed()
		if not device_name.strip():
			raise ValueError("device_name cannot be empty")
		return _handle_value(self._native.connect_ble(device_name))

	def connect_serial(self, port_name: str) -> int:
		self._throw_if_disposed()
		if not port_name.strip():
			raise ValueError("port_name cannot be empty")
		return _handle_value(self._native.connect_serial(port_name))

	def disconnect(self, device_handle: int = 0) -> bool:
		"""
		Disconnect one device, or all devices when device_handle is 0.
		"""
		self._throw_if_disposed()
		return bool(self._native.disconnect(_as_void_p(device_handle)))

	def get_connected_device_count(self) -> int:
		self._throw_if_disposed()
		return int(self._native.get_connected_device_count())

	def try_get_connected_device_handle(self, index: int) -> tuple[bool, int]:
		self._throw_if_disposed()
		if index < 0:
			raise ValueError("index cannot be negative")

		output = ctypes.c_void_p()
		success = bool(
			self._native.get_connected_device_handle(
				index,
				ctypes.byref(output),
				)
				)
		return success, _handle_value(output)

	def get_connected_device_handles(self) -> list[int]:
		self._throw_if_disposed()
		handles: list[int] = []

		for index in range(self.get_connected_device_count()):
			success, handle = self.try_get_connected_device_handle(index)
			if success and handle:
				handles.append(handle)

		return handles

	# ------------------------------------------------------------------
	# Information requests
	# ------------------------------------------------------------------

	def request_device_name(self, device_handle: int) -> bool:
		self._throw_if_disposed()
		return bool(self._native.get_device_name(_as_void_p(device_handle)))

	def request_firmware_version(self, device_handle: int) -> bool:
		self._throw_if_disposed()
		return bool(self._native.get_fw_version(_as_void_p(device_handle)))

	def request_hardware_version(self, device_handle: int) -> bool:
		self._throw_if_disposed()
		return bool(self._native.get_hw_version(_as_void_p(device_handle)))

	def get_display_info(self, device_handle: int) -> DotPadDisplayInfo:
		self._throw_if_disposed()

		width = ctypes.c_int()
		height = ctypes.c_int()
		braille = ctypes.c_int()

		success = bool(
			self._native.get_display_info(
				_as_void_p(device_handle),
				ctypes.byref(width),
				ctypes.byref(height),
				ctypes.byref(braille),
				)
			)

		if not success:
			raise DotPadError("DOT_PAD_GET_DISPLAY_INFO failed")

		return DotPadDisplayInfo(
			width=width.value,
			height=height.value,
			has_braille=bool(braille.value),
			)

	# ------------------------------------------------------------------
	# Graphic display
	# ------------------------------------------------------------------

	def display_file(
		self,
		device_handle: int,
		display_file: str | os.PathLike[str],
		) -> bool:
		self._throw_if_disposed()

		path = os.fspath(display_file)
		encoded_path = os.fsencode(path)

		return bool(
			self._native.display_file(
				encoded_path,
				_as_void_p(device_handle),
				)
			)

	def display_data(
		self,
		device_handle: int,
		data: bytes | bytearray | memoryview,
		*,
		retain_until_callback: bool = True,
		) -> bool:
		self._throw_if_disposed()

		copied = bytes(data)
		if not copied:
			raise ValueError("data cannot be empty")

		native_buffer = (ctypes.c_uint8 * len(copied)).from_buffer_copy(copied)
		handle = _handle_value(device_handle)

		if retain_until_callback:
			# Retain the buffer in case the SDK reads it asynchronously.
			self._display_buffers[handle] = native_buffer

		success = bool(
			self._native.display_data(
				native_buffer,
				len(copied),
				_as_void_p(handle),
				)
			)

		if not success:
			self._display_buffers.pop(handle, None)

		return success

	def reset_display(self, device_handle: int) -> bool:
		self._throw_if_disposed()
		return bool(self._native.reset_display(_as_void_p(device_handle)))

	# ------------------------------------------------------------------
	# Braille and text display
	# ------------------------------------------------------------------

	def display_braille(
		self,
		device_handle: int,
		text: str,
		*,
		language: DotPadLanguage | int = DotPadLanguage.ENGLISH,
		grade: int = 2,
		english_grade_if_korean: int = 2,
		) -> bool:
		self._throw_if_disposed()

		return bool(
			self._native.braille_display(
				text,
				int(language),
				int(grade),
				int(english_grade_if_korean),
				_as_void_p(device_handle),
				self._braille_callback,
				)
			)

	def display_braille_data(
		self,
		device_handle: int,
		braille_data: bytes | bytearray | memoryview,
		) -> bool:
		self._throw_if_disposed()

		copied = bytes(braille_data)
		if not copied:
			raise ValueError("braille_data cannot be empty")

		native_buffer = (ctypes.c_uint8 * len(copied)).from_buffer_copy(copied)

		return bool(
			self._native.braille_display_data(
				native_buffer,
				len(copied),
				_as_void_p(device_handle),
				)
			)

	def display_braille_ascii(
		self,
		device_handle: int,
		braille_ascii: str | bytes,
		) -> bool:
		self._throw_if_disposed()

		if isinstance(braille_ascii, str):
			encoded = braille_ascii.encode("ascii")
		else:
			encoded = bytes(braille_ascii)

		return bool(
			self._native.braille_ascii_display(
				encoded,
				_as_void_p(device_handle),
				)
			)

	def reset_braille_display(self, device_handle: int) -> bool:
		self._throw_if_disposed()
		return bool(
			self._native.reset_braille_display(
				_as_void_p(device_handle)
				)
			)

	def set_language(
		self,
		language: DotPadLanguage | int,
		grade: int,
		) -> None:
		self._throw_if_disposed()
		self._native.set_language(int(language), int(grade))

	def set_english_grade_if_korean(self, grade: int) -> None:
		self._throw_if_disposed()
		self._native.set_english_grade_if_korean(int(grade))

	# ------------------------------------------------------------------
	# Native callbacks
	# ------------------------------------------------------------------

	def _on_ble_scan(self, device_name: str | None) -> None:
		try:
			name = (device_name or "").strip()
			if name:
				self._dispatch(self.on_ble_device_found, name)
		except Exception as error:
			self._log(f"BLE scan callback failed: {error!r}")

	def _on_usb_scan(self, port_name: str | None) -> None:
		try:
			name = (port_name or "").strip()
			if name:
				self._dispatch(self.on_usb_port_found, name)
		except Exception as error:
			self._log(f"USB scan callback failed: {error!r}")

	def _on_key_callback(
		self,
		device_handle: int | None,
		key_code: int,
		message_ptr: int | None,
		) -> None:
		try:
			handle = _handle_value(device_handle)

			if message_ptr:
				raw_message = ctypes.string_at(message_ptr)
				message = _decode_message(raw_message)
			else:
				message = ""
				
			key = _safe_enum(DotKeyCode, key_code)
			
			self._log(
				f"Key callback: handle=0x{handle:X}, "
				f"key={key!r}, message={message!r}"
				)
				
			self._dispatch(
				self.on_key_pressed,
				handle,
				key,
				message,
				)

		except Exception as error:
			self._log(f"Key callback failed: {error!r}")

	def _on_message_callback(
		self,
		device_handle: int | None,
		message_code: int,
		raw_message: bytes | None,
		) -> None:
		try:
			self._dispatch(
				self.on_message_received,
				_handle_value(device_handle),
				_safe_enum(DotDataCode, message_code),
				_decode_message(raw_message),
				)
		except Exception as error:
			self._log(f"Message callback failed: {error!r}")

	def _on_display_callback(self, device_handle: int | None) -> None:
		try:
			handle = _handle_value(device_handle)
			self._display_buffers.pop(handle, None)
			self._dispatch(self.on_display_completed, handle)
		except Exception as error:
			self._log(f"Display callback failed: {error!r}")


	def _on_braille_translated_callback(
		self,
		device_handle: int | None,
		translated_data_pointer: int | None,
		data_size: int,
		) -> None:
		try:
			handle = int(device_handle or 0)
			size = int(data_size)

			if not translated_data_pointer or size <= 0:
				managed_data = b""
			else:
				managed_data = ctypes.string_at(
					translated_data_pointer,
					size,
					)

			self._log(
				"Braille translation callback: "
				f"handle=0x{handle:X}, size={size}, "
				f"data={managed_data.hex(' ')}"
				)

			self._dispatch(
				self.on_braille_translated,
				handle,
				managed_data,
				)

		except Exception as error:
			self._log(
				"Braille translation callback failed: "
				f"{error!r}"
				)

	def _org_on_braille_translated_callback(
		self,
		device_handle: int | None,
		translated_data: ctypes.POINTER(ctypes.c_uint8),
		data_size: int,
		) -> None:
		try:
			handle = _handle_value(device_handle)
			size = int(data_size)

			self._log(
				"Native Braille translation callback: "
				f"handle=0x{handle:X}, size={size}, "
				f"pointer={bool(translated_data)}"
				)

			if not translated_data or size <= 0:
				translated = b""
			else:
				translated = ctypes.string_at(translated_data, size)

			self._log(
				"Translated Braille data: "
				f"{translated.hex(' ')}"
				)

			self._dispatch(
				self.on_braille_translated,
				handle,
				translated,
			)
		except Exception as error:
			self._log(f"Braille translation callback failed: {error!r}")

	# ------------------------------------------------------------------
	# Lifecycle
	# ------------------------------------------------------------------

	def close(self) -> None:
		with self._lock:
			if self._disposed:
				return

			try:
				self._native.ble_scan_stop()
			except Exception:
				pass

			try:
				# A null handle disconnects all devices.
				self._native.disconnect(ctypes.c_void_p())
			except Exception:
				pass

			self._disposed = True
			self._display_buffers.clear()

			self.on_ble_device_found = None
			self.on_usb_port_found = None
			self.on_key_pressed = None
			self.on_message_received = None
			self.on_display_completed = None
			self.on_braille_translated = None

			self._native.close()

	def __enter__(self) -> "DotPadSdkClient":
		self._throw_if_disposed()
		return self

	def __exit__(self, exc_type, exc_value, traceback) -> None:
		self.close()


	def resetDataBuffer(self):
		self._data = ctypes.c_buffer(self.hCellCount * self.vCellCount)

	def setDotInDataBuffer(self, x: int, y: int):
		if x < 0 or x >= self.hPixelCount or y < 0 or y >= self.vPixelCount:
			return
		vCellIndex = int(y / self.cellHeight)
		hCellIndex = int(x / self.cellWidth)
		cellIndex = (vCellIndex * self.hCellCount) + hCellIndex
		bit = (y % self.cellHeight) + ((x % self.cellWidth) * self.cellHeight)
		self._data[cellIndex] = ord(self._data[cellIndex]) | 2**bit

	def getDisplayInfo(self):
		self.hCellCount, self.vCellCount, self.bCellCount = dotPadSdk.getDisplayInfo()
		self.hPixelCount = self.hCellCount * self.cellWidth
		self.vPixelCount = self.vCellCount * self.cellHeight


__all__ = [
	"BrailleTranslateCallback",
	"DeviceHandle",
	"DisplayCallback",
	"DotDataCode",
	"DotKeyCode",
	"DotPadDisplayInfo",
	"DotPadDisposedError",
	"DotPadError",
	"DotPadLanguage",
	"DotPadLoadError",
	"DotPadNative",
	"DotPadSdkClient",
	"KeyCallback",
	"MessageCallback",
	"ScanCallback",
]
