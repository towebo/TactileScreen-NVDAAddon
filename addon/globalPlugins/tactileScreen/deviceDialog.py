# deviceDialog.py

from __future__ import annotations

from collections.abc import Callable

import wx

import gui
from logHandler import log

from .DotPadSdkClient import DotPadSdkClient


class DotPadDeviceDialog(wx.Dialog):
    """Scans for BLE DotPad devices and lets the user select one."""

    def __init__(
        self,
        parent: wx.Window,
        client: DotPadSdkClient,
        connect_callback: Callable[[str, int], None],
    ) -> None:
        super().__init__(
            parent,
            title="Connect to DotPad",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )

        self._client = client
        self._connect_callback = connect_callback

        self._scanning = False
        self._closing = False
        self._devices: set[str] = set()

        self._build_ui()
        self._bind_events()

        self.SetSize((500, 350))
        self.CentreOnParent()

    def _build_ui(self) -> None:
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        instructions = wx.StaticText(
            self,
            label="Select a discovered Bluetooth DotPad device:",
        )
        main_sizer.Add(
            instructions,
            flag=wx.ALL | wx.EXPAND,
            border=10,
        )

        self.device_list = wx.ListBox(
            self,
            choices=[],
            style=wx.LB_SINGLE,
            name="Discovered DotPad devices",
        )
        main_sizer.Add(
            self.device_list,
            proportion=1,
            flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
            border=10,
        )

        self.status_label = wx.StaticText(
            self,
            label="Scanning for devices…",
        )
        main_sizer.Add(
            self.status_label,
            flag=wx.ALL | wx.EXPAND,
            border=10,
        )

        button_sizer = wx.StdDialogButtonSizer()

        self.connect_button = wx.Button(
            self,
            wx.ID_OK,
            label="Connect",
        )
        self.connect_button.Disable()

        self.cancel_button = wx.Button(
            self,
            wx.ID_CANCEL,
            label="Cancel",
        )

        button_sizer.AddButton(self.connect_button)
        button_sizer.AddButton(self.cancel_button)
        button_sizer.Realize()

        main_sizer.Add(
            button_sizer,
            flag=wx.ALL | wx.ALIGN_RIGHT,
            border=10,
        )

        self.SetSizer(main_sizer)

    def _bind_events(self) -> None:
        self.Bind(wx.EVT_SHOW, self._on_show)
        self.Bind(wx.EVT_CLOSE, self._on_close)

        self.device_list.Bind(
            wx.EVT_LISTBOX,
            self._on_selection_changed,
        )
        self.device_list.Bind(
            wx.EVT_LISTBOX_DCLICK,
            self._on_device_activated,
        )

        self.connect_button.Bind(
            wx.EVT_BUTTON,
            self._on_connect,
        )
        self.cancel_button.Bind(
            wx.EVT_BUTTON,
            self._on_cancel,
        )

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _on_show(self, event: wx.ShowEvent) -> None:
        event.Skip()

        if event.IsShown() and not self._scanning:
            # Start after the dialog has become visible and focusable.
            wx.CallAfter(self._start_scan)

    def _start_scan(self) -> None:
        if self._closing or self._scanning:
            return

        try:
            self._client.on_ble_device_found = self.add_device
            self._client.start_ble_scan()

            self._scanning = True
            self.status_label.SetLabel("Scanning for devices…")
            self.device_list.SetFocus()

        except Exception:
            log.exception("Could not start DotPad BLE scanning")
            self.status_label.SetLabel(
                "Could not start Bluetooth scanning."
            )

            gui.messageBox(
                "NVDA could not start scanning for DotPad devices.",
                "DotPad",
                wx.OK | wx.ICON_ERROR,
                parent=self,
            )

    def _stop_scan(self) -> None:
        if not self._scanning:
            return

        self._scanning = False

        try:
            self._client.stop_ble_scan()
        except Exception:
            log.exception("Could not stop DotPad BLE scanning")

    def add_device(self, device_name: str) -> None:
        """Called on NVDA's main thread when the SDK finds a device."""
        if self._closing:
            return

        name = device_name.strip()
        if not name or name in self._devices:
            return

        self._devices.add(name)
        self.device_list.Append(name)

        if self.device_list.GetSelection() == wx.NOT_FOUND:
            self.device_list.SetSelection(0)
            self.connect_button.Enable()

        count = len(self._devices)
        if count == 1:
            self.status_label.SetLabel("1 device found. Scanning…")
        else:
            self.status_label.SetLabel(
                f"{count} devices found. Scanning…"
            )

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    def _on_selection_changed(
        self,
        event: wx.CommandEvent,
    ) -> None:
        has_selection = (
            self.device_list.GetSelection() != wx.NOT_FOUND
        )
        self.connect_button.Enable(has_selection)
        event.Skip()

    def _on_device_activated(
        self,
        event: wx.CommandEvent,
    ) -> None:
        self._connect_selected_device()

    def _on_connect(self, event: wx.CommandEvent) -> None:
        self._connect_selected_device()

    def _connect_selected_device(self) -> None:
        selection = self.device_list.GetSelection()

        if selection == wx.NOT_FOUND:
            wx.Bell()
            self.status_label.SetLabel(
                "Select a device before connecting."
            )
            self.device_list.SetFocus()
            return

        device_name = self.device_list.GetString(selection)

        self.connect_button.Disable()
        self.cancel_button.Disable()
        self.device_list.Disable()
        self.status_label.SetLabel(
            f"Connecting to {device_name}…"
        )

        # Stop discovery before starting a connection.
        self._stop_scan()

        try:
            device_handle = self._client.connect_ble(device_name)

            if not device_handle:
                self.device_list.Enable()
                self.cancel_button.Enable()
                self.connect_button.Enable()

                self.status_label.SetLabel(
                    f"Could not connect to {device_name}."
                )

                gui.messageBox(
                    f"Could not start a connection to {device_name}.",
                    "DotPad",
                    wx.OK | wx.ICON_ERROR,
                    parent=self,
                )
                return

            # Save the pending handle in the owning plugin.
            self._connect_callback(
                device_name,
                device_handle,
            )

            # The actual connection is confirmed asynchronously by the
            # DOT_DATA_CODE_CONNECTED message.
            self._closing = True
            self.EndModal(wx.ID_OK)

        except Exception:
            log.exception(
                "Could not connect to DotPad device %r",
                device_name,
            )

            self.device_list.Enable()
            self.cancel_button.Enable()
            self.connect_button.Enable()

            self.status_label.SetLabel(
                f"Could not connect to {device_name}."
            )

            gui.messageBox(
                f"NVDA could not connect to {device_name}.",
                "DotPad",
                wx.OK | wx.ICON_ERROR,
                parent=self,
            )

    def _on_cancel(self, event: wx.CommandEvent) -> None:
        self._close_dialog(wx.ID_CANCEL)

    def _on_close(self, event: wx.CloseEvent) -> None:
        self._close_dialog(wx.ID_CANCEL)

    def _close_dialog(self, result: int) -> None:
        if self._closing:
            return

        self._closing = True
        self._stop_scan()

        # Prevent a late queued scan result from targeting this dialog.
        if self._client.on_ble_device_found == self.add_device:
            self._client.on_ble_device_found = None

        if self.IsModal():
            self.EndModal(result)
        else:
            self.Destroy()