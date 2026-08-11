		# A part of the DotPad NVDA add-on.
# A part of the DotPad NVDA add-on.
# Copyright (C) 2022 NV Access Limited.
# this code is licensed under the GNU General Public License version 2.


import math
import ctypes
import time
import wx
import core

from typing import Any, Callable
from pathlib import Path
from logHandler import log

from .dotPadSdk import (
	DotDataCode,
	DotKeyCode,
	DotPadLanguage,
	DotPadDisplayInfo,
	DotPadError,
	DotPadLoadError,
	ScanCallback,
)
from .DotPadSdkClient import (
	DotPadSdkClient,
)
from .deviceDialog import DotPadDeviceDialog


import globalPluginHandler
import tones
import queueHandler
from scriptHandler import script, getLastScriptRepeatCount
import ui
import config
import api
import gui
from gui.settingsDialogs import SettingsDialog
from gui import guiHelper
import hwPortUtils
from .imageUtils import StretchMode, captureImage, getMonochromePixelUsingLocalBrightnessThreshold
from locationHelper import RectLTRB
import ctypes
from ctypes import wintypes


class POINT(ctypes.Structure):
	_fields_ = [
		("x", wintypes.LONG),
		("y", wintypes.LONG),
		]

def getMousePosition():
	pt = POINT()
	ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
	return pt.x, pt.y

class DotPadConnectionDialog(SettingsDialog):
	title = "DotPad Connection"

	def __init__(self, parent, globalPlugin):
		self._globalPlugin = globalPlugin
		super().__init__(parent)

	def makeSettings(self,settingsSizer):
		conf = config.conf[self._globalPlugin._configName]
		settingsSizerHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		curPort = conf['port']
		self._possiblePorts = [x['port'] for x in hwPortUtils.listComPorts()]
		self._possiblePorts.insert(0, "[Not set]")
		if not curPort:
			index = 0
		else:
			try:
				index = self._possiblePorts.index(curPort)
			except ValueError:
				# Port no longer exists, but list it as missing
				index = 1
				self._possiblePorts.insert(index, f"{curPort} (missing)")
		self.portList = settingsSizerHelper.addLabeledControl("Dot Pad COM port", wx.Choice, choices=self._possiblePorts)
		self.portList.SetSelection(index)

	def postInit(self):
		self.portList.SetFocus()

	def onOk(self, evt):
		index = self.portList.GetSelection()
		if index != 0:
			port = self._possiblePorts[index].split(' ')[0]
			try:
				self._globalPlugin.initDotPad(port)
			except (DotPadError, RuntimeError) as e:
				gui.messageBox(f"{e}", "Error")
				self.portList.SetFocus()
				return
		else:
			self._globalPlugin.terminateDotPad()
			port = ""
		conf = config.conf[self._globalPlugin._configName]
		conf['port'] = port
		super().onOk(evt)


REFRESH_INTERVAL_MS = 1000

class GlobalPlugin(globalPluginHandler.GlobalPlugin):

	curInstance = None

	cur_display_width = 60
	cur_display_height = 40
	curCenterX = 0
	curCenterY = 0
	curViewPortWidth = 60
	curViewPortHeight  = 40
	curStepX = 20
	curStepY = 13
	
	_isTerminating = False
	_auto_refresh = False
	_refreshPending = False
	_track_nav_obj = False
	_last_nav_obj = None
	_track_mouse = False

	nav_obj_margin = 5

	_is_white_on_black = False

	_configName = 'tactileScreen'
	_configSpec = {
		'device': 'string(default="")',
	}

	def __init__(self):
		super().__init__()
		config.conf.spec[self._configName] = self._configSpec

		self._client: DotPadSdkClient | None = None
		self._deviceDialog: DotPadDeviceDialog | None = None

		# A handle returned by CONNECT_BLE means that a connection attempt
		# started. The device is not fully connected until CONNECTED arrives
		# through the message callback.
		self._pending_device_handle = 0
		self._pending_device_name = ""

		self._device_handle = 0
		self._connected_device_name = ""

		self._initialize_dotpad_api()

		self._isTerminating = False
		self._refreshPending = False

		if self._client is not None:
			stored_device = config.conf[self._configName]["device"]
			if stored_device:
				self._autoConnectDevice = stored_device
				self._client.start_ble_scan()
			else:
				self._autoConnectDevice = ""


	def terminate(self) -> None:
		try:
			self._isTerminating = True
			client = self._client
			self._client = None

			if client is not None:
				try:
					client.close()
				except Exception:
					log.exception(
						"Error closing the DotPad SDK"
					)

			self._device_handle = 0
			self._pending_device_handle = 0
			self._connected_device_name = ""
			self._pending_device_name = ""

		finally:
			super().terminate()



	def _initialize_dotpad_api(self) -> None:
		plugin_directory = Path(__file__).resolve().parent
		api_directory = plugin_directory / "SDK"
		dll_path = api_directory / "DotPadSDK-3.0.0.dll"

		log.info("Initializing DotPad SDK from %s", dll_path)

		try:
			self._client = DotPadSdkClient(
				dll_path,
				log=self._log_sdk_message,
				dispatch=self._dispatch_to_nvda_thread,
			)

			self._client.on_ble_device_found = (
				self._on_ble_device_found
			)
			self._client.on_usb_port_found = (
				self._on_usb_port_found
			)
			self._client.on_key_pressed = (self._on_key_pressed)
			self._client.on_message_received = (
				self._on_message_received
			)
			#tmp self._client.on_display_completed = (
			#	self._on_display_completed
			#)
			#tmp self._client.on_braille_translated = (
			#	self._on_braille_translated
			#)

		except DotPadLoadError as error:
			self._client = None
			log.exception("Could not load the DotPad SDK")
			ui.message(f"Could not load the DotPad SDK: {error}")

		except Exception:
			self._client = None
			log.exception("Unexpected error initializing DotPad")
			ui.message("Could not initialize the DotPad SDK")

		else:
			log.info("DotPad SDK initialized successfully")

	@staticmethod
	def _log_sdk_message(message: str) -> None:
		log.info("DotPad SDK: %s", message)

	@staticmethod
	def _dispatch_to_nvda_thread(
		function: Callable[..., None],
		*args: Any,
	) -> None:
		"""
		DotPad callbacks may run on SDK-created threads.

		Queue callback processing onto NVDA's event queue before accessing
		NVDA objects, wx controls, speech, or other UI components.
		"""
		queueHandler.queueFunction(
			queueHandler.eventQueue,
			function,
			*args,
		)

	def _require_client(self) -> DotPadSdkClient | None:
		if self._client is None:
			ui.message("The DotPad SDK is not available")
			return None

		if self._client.disposed:
			ui.message("The DotPad SDK has been closed")
			return None

		return self._client



	# ------------------------------------------------------------------
	# SDK event handlers
	# ------------------------------------------------------------------

	def _on_ble_device_found(self, device_name: str) -> None:
		log.info("DotPad BLE device found: %r", device_name)
		if self._deviceDialog is not None:
			self._deviceDialog.add_device(device_name)

		# Automatic connection.
		if (
			self._autoConnectDevice
			and device_name == self._autoConnectDevice
			):
			wanted_device = self._autoConnectDevice

			# Clear it immediately so repeated scan callbacks don't initiate
			# multiple connection attempts.
			self._autoConnectDevice = ""

			try:
				self._client.stop_ble_scan()
				handle = self._client.connect_ble(wanted_device)

				if not handle:
					log.warning(
						"Automatic connection to %r could not be started",
						wanted_device,
						)
					return

				self._pending_device_handle = handle
				self._pending_device_name = wanted_device

				log.info(
					"Automatic DotPad connection started: "
					"name=%r, handle=0x%X",
					wanted_device,
					handle,
					)

			except Exception:
				log.exception(
					"Automatic connection to %r failed",
					wanted_device,
					)


	def _on_usb_port_found(self, port_name: str) -> None:
		log.info("DotPad serial port found: %r", port_name)

	# ------------------------------------------------------------------
	# Connection method used by the BLE scanning dialog
	# ------------------------------------------------------------------

	def connect_to_ble_device(self, device_name: str) -> bool:
		client = self._require_client()
		if client is None:
			return False

		try:
			client.stop_ble_scan()
		except Exception:
			# Scanning may already have stopped.
			log.debugWarning(
				"Could not stop DotPad BLE scan before connection",
				exc_info=True,
			)

		try:
			handle = client.connect_ble(device_name)
		except Exception:
			log.exception(
				"Could not connect to DotPad %r",
				device_name,
			)
			ui.message(f"Could not connect to {device_name}")
			return False

		if not handle:
			ui.message(f"Could not connect to {device_name}")
			return False

		self._pending_device_handle = handle
		self._pending_device_name = device_name

		log.info(
			"DotPad connection attempt started: "
			"name=%r, handle=0x%X",
			device_name,
			handle,
		)
		ui.message(f"Connecting to {device_name}")

		return True


	def _on_connection_started(
		self,
		device_name: str,
		device_handle: int,
		) -> None:
		self._pending_device_name = device_name
		self._pending_device_handle = device_handle
		
		log.info(
			"Connection started to %s (0x%X)",
			device_name,
			device_handle,
			)


	def outputDataBuffer(self, client, fullRefresh=False) -> bool:
		#tmp self._displayDoneEvent.clear()
		client.display_data(
			self._device_handle,
			client._data
		)
		#tmp self._displayDoneEvent.wait(3)


	def _showDeviceDialog(self) -> None:
		if self._deviceDialog is not None:
			try:
				self._deviceDialog.Raise()
				self._deviceDialog.SetFocus()
				return
			except RuntimeError:
				self._deviceDialog = None

		dialog =	 None

		try:
			log.debug("DotPad: calling prePopup")
			gui.mainFrame.prePopup()
			log.debug("DotPad: prePopup completed")

			dialog = DotPadDeviceDialog(
				gui.mainFrame,
				self._client,
				self._on_connection_started,
				)
			self._deviceDialog = dialog

			log.debug("DotPad: showing device dialog")
			dialog.ShowModal()
			log.debug("DotPad: device dialog closed")
		except Exception:
			log.exception("Could not show the DotPad device dialog")
			ui.message("Could not open the DotPad device dialog")

		finally:
			self._deviceDialog = None
			if dialog is not None:
				try:
					dialog.Destroy()
				except Exception:
					log.exception("Could not destroy the DotPad dialog")

			try:
				gui.mainFrame.postPopup()
			except Exception:
				log.exception("DotPad postPopup failed")

	def _clearDotPadDisplays(self, device_handle: int) -> bool:
		client = self._client
		if client is None:
			log.error("Cannot clear DotPad displays: SDK client is unavailable")
			return False

		if not self.handle:
			log.error("Cannot clear DotPad displays: invalid device handle")
			return False

		graphic_success = False
		braille_success = False

		try:
			graphic_success = client.reset_display(self.handle)
			log.info(
				"DOT_PAD_RESET_DISPLAY(handle=0x%X) returned %s",
				self.handle,
				graphic_success,
			)
		except Exception:
			log.exception("Could not reset the DotPad graphical display")

		try:
			braille_success = client.reset_braille_display(self.handle)
			log.info(
				"DOT_PAD_RESET_BRAILLE_DISPLAY(handle=0x%X) returned %s",
				self.handle,
				braille_success,
			)
		except Exception:
			log.exception("Could not reset the DotPad Braille display")

		return graphic_success and braille_success


	def _scheduleRefresh(self):
		if self._isTerminating or self._refreshPending:
			return

		self._refreshPending = True
		core.callLater(
			REFRESH_INTERVAL_MS,
			self._onRefreshTimer,
		)

	def _onRefreshTimer(self):
		self._refreshPending = False

		if self._isTerminating:
			return

		try:
			if self._track_nav_obj and self._last_nav_obj != api.getNavigatorObject():
				self.showNavigatorObject(self._is_white_on_black)
				return
			elif self._track_mouse:
				self.showMousePointerObject(self._is_white_on_black)
				return

			location = self.get_view_rect(self.curCenterX, self.curCenterY)
			location = self.adjust_rect(location)
			self.displayScreenLocation(location, self._is_white_on_black)
		except Exception:
			# Prevent one failed refresh from stopping future refreshes.
			log.exception("Error refreshing tactile display")
		finally:
			if self._auto_refresh:
				self._scheduleRefresh()


	def _on_key_pressed(
		self,
		device_handle: int,
		key_code: DotKeyCode | int,
		message: str,
		) -> None:
		log.info(
			"DotPad key callback: handle=0x%X, key=%r, message=%r",
			device_handle,
			key_code,
			message,
			)

		self._handle_key_press(int(key_code))

	def _on_message_received(
		self,
		device_handle: int,
		message_code: DotDataCode | int,
		message: str,
		) -> None:
		log.info("DotPad message: handle=0x%X, code=%r, message=%r",
		device_handle,
		message_code,
		message,
		)
		
		# Unknown future SDK message code.
		if not isinstance(message_code, DotDataCode):
			log.warning("Unknown DotPad message code %r from handle 0x%X",
				message_code,
				device_handle,
				)
			return

		if message_code == DotDataCode.CONNECTED:
			self._device_handle = device_handle
			
			if device_handle == self._pending_device_handle:
				self._connected_device_name = self._pending_device_name
			self._pending_device_handle = 0
			self._pending_device_name = ""

			config.conf[self._configName]["device"] = self._connected_device_name

			log.info("DotPad connected: handle=0x%X, name=%r",
				device_handle,
				self._connected_device_name,
				)

			ui.message(f"Connected to {self._connected_device_name}"
				if self._connected_device_name
				else "DotPad connected"
				)

			client = self._require_client()
			client.get_display_info(self._device_handle)
			client.resetDataBuffer()
			client.reset_display(self._device_handle)
			client.reset_braille_display(self._device_handle)
		

			# Ask the device for its authoritative name.
			try:
				if self._client is not None:
					self._client.request_device_name(device_handle)
			except Exception:
				log.exception("Could not request DotPad device name")

			# Optional information requests.
			try:
				if self._client is not None:
					self._client.request_firmware_version(device_handle)
					self._client.request_hardware_version(device_handle)
			except Exception:
				log.exception("Could not request DotPad version information")

		elif message_code == DotDataCode.DISCONNECTED:
			log.info("DotPad disconnected: handle=0x%X",
				device_handle,
				)

			if device_handle == self._device_handle:
				self._device_handle = 0
				self._connected_device_name = ""

			if device_handle == self._pending_device_handle:
				self._pending_device_handle = 0
				self._pending_device_name = ""

			ui.message("DotPad disconnected")

		elif message_code == DotDataCode.BOARD_INFO:
			# Your earlier logs showed this can contain binary-looking data,
			# so don't treat it as normal user-facing text.
			log.info(
				"DotPad board info: handle=0x%X, data=%r",
				device_handle,
				message,
				)

		elif message_code == DotDataCode.BLE_MAC_ADDRESS:
			log.info(
				"DotPad BLE MAC address: handle=0x%X, address=%r",
				device_handle,
				message,
				)

		elif message_code == DotDataCode.DEVICE_NAME:
			device_name = message.strip()
			if device_name:
				self._connected_device_name = device_name
				
				
				log.info(
					"DotPad device name received and stored: %r",
					device_name,
					)
			else:
				log.warning(
					"DotPad returned an empty device name for handle 0x%X",
					device_handle,
					)

		elif message_code == DotDataCode.DEVICE_FW_VERSION:
			log.info(
				"DotPad firmware version: %r",
				message,
				)

		elif message_code == DotDataCode.DEVICE_HW_VERSION:
			log.info(
				"DotPad hardware version: %r",
				message,
				)

		elif message_code == DotDataCode.RESPONSE_DISPLAY_LINE_ACK:
			log.debug(
				"DotPad display ACK: handle=0x%X, line=%r",
				device_handle,
				message,
				)

		elif message_code == DotDataCode.RESPONSE_DISPLAY_LINE_NON_ACK:
			log.warning(
				"DotPad display NON-ACK: handle=0x%X, line=%r",
				device_handle,
				message,
				)

		elif message_code == DotDataCode.RESPONSE_DISPLAY_LINE_COMPLETE:
			log.debug(
				"DotPad display complete: handle=0x%X, line=%r",
				device_handle,
				message,
				)

		elif message_code == DotDataCode.COMMAND_ERROR:
			log.error(
				"DotPad command error: handle=0x%X, message=%r",
				device_handle,
				message,
				)

			if message:
				ui.message(f"DotPad error: {message}")
			else:
				ui.message("DotPad command error")

		elif message_code == DotDataCode.COMMAND_NONE:
			log.debug(
				"DotPad command-none message: handle=0x%X, message=%r",
				device_handle,
				message,
				)


	def _handle_key_press(self, keyCode):
		display_index = wx.Display.GetFromPoint((self.curCenterX, self.curCenterY))
		# Fallback to primary display
		if display_index == wx.NOT_FOUND:
			display_index = 0
		geo = wx.Display(display_index).GetGeometry()

		# Zoom out
		if keyCode == DotKeyCode.FUNCTION3:
			self.curViewPortWidth = self.curViewPortWidth  + self.cur_display_width
			self.curViewPortHeight = self.curViewPortHeight + self.cur_display_height
			# Don't grow beyond screen size
			self.curViewPortWidth  = min(self.curViewPortWidth, geo.width)
			self.curViewPortHeight = min(self.curViewPortHeight, geo.height)

			self.curStepX = self.curViewPortWidth // 3
			self.curStepY = self.curViewPortHeight // 3

			location = self.get_view_rect(self.curCenterX, self.curCenterY)
			location = self.adjust_rect(location)
			self.displayScreenLocation(location, self._is_white_on_black)
			#ui.message("Zoom out, %d" % self.curViewPortWidth)
			
		# Zoom in
		elif keyCode == DotKeyCode.FUNCTION2:
			self.curViewPortWidth = self.curViewPortWidth  - self.cur_display_width
			self.curViewPortHeight = self.curViewPortHeight - self.cur_display_height
			# Don't shrink below pad size
			self.curViewPortWidth  = max(self.cur_display_width, self.curViewPortWidth)
			self.curViewPortHeight = max(self.cur_display_height, self.curViewPortHeight)

			self.curStepX = self.curViewPortWidth // 3
			self.curStepY = self.curViewPortHeight // 3

			location = self.get_view_rect(self.curCenterX, self.curCenterY)
			location = self.adjust_rect(location)
			self.displayScreenLocation(location, self._is_white_on_black)
			#ui.message("Zoom in, %d" % self.curViewPortWidth)
			
		# Move down
		elif keyCode == DotKeyCode.FUNCTION4:
			self.curCenterY = self.curCenterY + self.curStepY
			
			location = self.get_view_rect(self.curCenterX, self.curCenterY)
			location = self.adjust_rect(location)
			self.displayScreenLocation(location, self._is_white_on_black)
			#ui.message("Move down, %d" % location.top)

		# Move up
		elif keyCode == DotKeyCode.FUNCTION1:
			self.curCenterY = self.curCenterY - self.curStepY
			location = self.get_view_rect(self.curCenterX, self.curCenterY)
			location = self.adjust_rect(location)
			self.displayScreenLocation(location, self._is_white_on_black)
			#ui.message("Move up, %d" % location.top)

		# Move right
		elif keyCode == DotKeyCode.PANNING_RIGHT:
			self.curCenterX = self.curCenterX + self.curStepX
			location = self.get_view_rect(self.curCenterX, self.curCenterY)
			location = self.adjust_rect(location)
			self.displayScreenLocation(location, self._is_white_on_black)
			#ui.message("Move right, %d" % location.left)

		# Move left
		elif keyCode == DotKeyCode.PANNING_LEFT:
			self.curCenterX = self.curCenterX - self.curStepX
			location = self.get_view_rect(self.curCenterX, self.curCenterY)
			location = self.adjust_rect(location)
			self.displayScreenLocation(location, self._is_white_on_black)
			#ui.message("Move left, %d" % location.left)


	def showNavigatorObject(self, isWhiteOnBlack=False):
		self._last_nav_obj = api.getNavigatorObject()
		nav_location = self._last_nav_obj.location

		x = nav_location.left + (self.curViewPortWidth // 2) - self.nav_obj_margin
		y = nav_location.top + (self.curViewPortHeight // 2) - self.nav_obj_margin
		self.curCenterX = x
		self.curCenterY = y
		location = self.get_view_rect(self.curCenterX, self.curCenterY)
		location = self.adjust_rect(location)
		self.displayScreenLocation(location, isWhiteOnBlack=isWhiteOnBlack)

	def showMousePointerObject(self, isWhiteOnBlack=False):
		x, y = getMousePosition()
		self.curCenterX = x
		self.curCenterY = y
		location = self.get_view_rect(self.curCenterX, self.curCenterY)
		location = self.adjust_rect(location)
		self.displayScreenLocation(location, isWhiteOnBlack=isWhiteOnBlack)


	def get_view_rect(self, center_x: int, center_y: int) -> RectLTRB:
		left = center_x - (self.curViewPortWidth // 2)
		top = center_y - (self.curViewPortHeight // 2)
		return RectLTRB(
			left=left,
			top=top,
			right = left + self.curViewPortWidth, 
			bottom=top + self.curViewPortHeight,
			)


	def adjust_rect(self, rect: RectLTRB) -> RectLTRB:
		display_index = wx.Display.GetFromPoint((rect.left, rect.top))
		# Fallback to primary display
		if display_index == wx.NOT_FOUND:
			display_index = 0
			
		geo = wx.Display(display_index).GetGeometry()
		
		# Clamp to screen origin
		left = max(0, rect.left)
		left = min(geo.width - rect.width, left)
		self.curCenterX = left + (rect.width // 2)

		top = max(0, rect.top)
		top = min(geo.height - rect.height, top)
		self.curCenterY = top + (rect.height // 2)

		return RectLTRB(
			left=left,
			top=top,
			right = left + self.curViewPortWidth, 
			bottom=top + self.curViewPortHeight,
		)



	def displayScreenLocation(self, location, isWhiteOnBlack=False):
		
		client = self._require_client()
		if not client:
			return
		stretchMode = StretchMode.WHITEONBLACK if isWhiteOnBlack else StretchMode.BLACKONWHITE
		image, (left, top, width, height) = captureImage(location.left, location.top, location.width, location.height, client.hPixelCount, client.vPixelCount, stretchMode=stretchMode)
		client.resetDataBuffer()
		for y in range(top, top+height):
			for x in range(left, left + width):
				isWhite = getMonochromePixelUsingLocalBrightnessThreshold(image, x, y, blur=3)
				isRaised = isWhite if isWhiteOnBlack else not isWhite
				if isRaised:
					client.setDotInDataBuffer(x, y)
		self.outputDataBuffer(client)

		text = f"{self.curCenterX:<5d}{self.curCenterY:<5d}{self.curViewPortWidth:<5d}{self.curViewPortHeight:<5d}"
		client.display_braille_ascii(
			self._device_handle,
			text,

		)

		

	@script(gesture="kb:NVDA+Escape")
	def script_stopTrackingAndAutoUpdate(self, gesture):
		self._track_mouse = False
		self._track_nav_obj = False
		self._auto_refresh = False
		ui.message("Stop Tracking !")

	@script(gesture="kb:NVDA+f8")
	def script_shownavigatorObject(self, gesture):
		self._track_mouse = False
		self._track_nav_obj = False

		if getLastScriptRepeatCount() == 1:
			self._track_nav_obj = True
			ui.message("Navigator Object Tracking enabled")

		client = self._require_client()
		if not client:
			return

		self.showNavigatorObject(self._is_white_on_black)
		self._auto_refresh = True;
		self._scheduleRefresh()

	@script(gesture="kb:shift+NVDA+f8")
	def script_show_mouse_pointer(self, gesture):
		self._track_mouse = False
		self._track_nav_obj = False
		if getLastScriptRepeatCount() == 1:
			self._track_mouse = True
			ui.message("Mouse Tracking enabled")

		client = self._require_client()
		if not client:
			return

		self.showMousePointerObject(self._is_white_on_black)
		self._auto_refresh = True;
		self._scheduleRefresh()


	@script(gesture="kb:control+NVDA+f8")	
	def script_showSettings(self, gesture):
		try:
			client = self._require_client()
			if client is None:
				return
				
			wx.CallAfter(self._showDeviceDialog)
		except exception as e:
			gui.messageBox(f"{e}", "Error")
		


	@script(
		description="Test DotPad Braille translation",
		gesture="kb:NVDA+shift+d",
		category="DotPad",)
	def script_displayTestText(self, gesture) -> None:
		client = self._require_client()
		if client is None:
			return

		text = "Hello Lennie"

		log.info(
			"Calling display_braille: "
			"handle=0x%X, text=%r, language=%d, grade=%d",
			self._device_handle,
			text,
			int(DotPadLanguage.ENGLISH),
			2,
		)

		try:
			# Set the global SDK language as well, even though the display
			# function also takes language and grade parameters.
			client.set_language(
				DotPadLanguage.ENGLISH,
				grade=2,
			)

			success = client.display_braille(
				self._device_handle,
				text,
				language=DotPadLanguage.ENGLISH,
				grade=2,
				english_grade_if_korean=2,
			)

			log.info(
				"display_braille returned %s",
				success,
			)

		except Exception:
			log.exception("DOT_PAD_BRAILLE_DISPLAY failed")
			ui.message("Braille display call failed")
			return

		if success:
			ui.message("Braille display command accepted")
		else:
			ui.message("Braille display command rejected")

