"""Background BLE advertisement scanner (bleak -> WinRT on Windows)."""
from __future__ import annotations

import asyncio
import logging
import threading

from PySide6.QtCore import QThread, Signal


class ScanWorker(QThread):
    """Runs an asyncio loop + BleakScanner off the GUI thread."""

    advertisement = Signal(str, str, int)   # address, name, rssi
    failed = Signal(str)                    # user-friendly message

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_flag = threading.Event()

    def request_stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> None:
        self._stop_flag.clear()
        try:
            asyncio.run(self._scan())
        except Exception as exc:  # noqa: BLE001 - surface any adapter/driver error
            logging.exception("Bluetooth scan failed")
            self.failed.emit(
                "Could not scan for Bluetooth devices.\n\n"
                "Check that Bluetooth is turned on (Settings > Bluetooth & devices) "
                "and that this PC has a Bluetooth LE adapter.\n\n"
                f"Details: {exc}"
            )

    async def _scan(self) -> None:
        from bleak import BleakScanner  # imported here so the module loads without Bluetooth

        def on_detect(device, adv) -> None:
            name = (adv.local_name or device.name or "").strip()
            if adv.rssi is not None:
                self.advertisement.emit(device.address, name, int(adv.rssi))

        async with BleakScanner(detection_callback=on_detect):
            while not self._stop_flag.is_set():
                await asyncio.sleep(0.1)
