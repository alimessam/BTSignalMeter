"""BT Signal Meter - live Bluetooth LE signal strength (RSSI) monitor."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import __version__
from core.signal import (
    DEFAULT_PATH_LOSS, DEFAULT_TX_POWER_1M, DeviceStats, DeviceTracker,
    estimate_distance_m, export_csv, quality,
)
from scanner import ScanWorker

APP_NAME = "BT Signal Meter"
QUALITY_COLORS = {
    "Excellent": "#2e9d4f",
    "Good": "#6aa22a",
    "Fair": "#d19a1e",
    "Weak": "#d9702b",
    "Very weak": "#cc3b3b",
}
COLUMNS = ["Name", "Address", "RSSI (dBm)", "Avg (dBm)", "Quality", "Est. distance", "Samples", "Last seen"]


def data_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    path = Path(base) / "BTSignalMeter"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging() -> None:
    logging.basicConfig(
        filename=data_dir() / "app.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    def hook(exc_type, exc, tb):
        logging.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(None, APP_NAME, f"An unexpected error occurred:\n{exc}\n\nDetails were written to app.log.")

    sys.excepthook = hook


def format_distance(metres: float) -> str:
    if metres >= 100:
        return "> 100 m"
    return f"~ {metres:.1f} m"


class SignalGraph(QWidget):
    """Rolling RSSI line chart for one device (last `window` seconds)."""

    Y_MIN, Y_MAX = -100, -30

    def __init__(self, window: float = 60.0) -> None:
        super().__init__()
        self.window = window
        self._points: list[tuple[float, int]] = []
        self._now = time.time()
        self.setMinimumHeight(170)
        self.setAccessibleName("Signal history graph")

    def set_data(self, points, now: float) -> None:
        self._points = list(points)
        self._now = now
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        text_color = pal.color(pal.ColorRole.Text)
        grid_color = QColor(text_color)
        grid_color.setAlpha(45)

        left, right, top, bottom = 46, 12, 10, 24
        w = self.width() - left - right
        h = self.height() - top - bottom
        if w <= 10 or h <= 10:
            return

        def y_of(dbm: float) -> float:
            dbm = max(self.Y_MIN, min(self.Y_MAX, dbm))
            return top + (self.Y_MAX - dbm) / (self.Y_MAX - self.Y_MIN) * h

        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1))
        p.setFont(small)
        for dbm in range(self.Y_MIN, self.Y_MAX + 1, 10):
            y = y_of(dbm)
            p.setPen(QPen(grid_color, 1))
            p.drawLine(QPointF(left, y), QPointF(left + w, y))
            p.setPen(text_color)
            p.drawText(0, int(y) - 8, left - 6, 16, Qt.AlignRight | Qt.AlignVCenter, str(dbm))
        p.setPen(text_color)
        p.drawText(left, self.height() - 20, w, 16, Qt.AlignLeft, f"-{int(self.window)} s")
        p.drawText(left, self.height() - 20, w, 16, Qt.AlignRight, "now")

        if not self._points:
            p.drawText(left, top, w, h, Qt.AlignCenter, "Select a device to see its signal history")
            return

        start = self._now - self.window
        pts = [(t, r) for t, r in self._points if t >= start]
        if not pts:
            return
        path = QPainterPath()
        for i, (t, r) in enumerate(pts):
            pt = QPointF(left + (t - start) / self.window * w, y_of(r))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        line = QColor("#2b7de9")
        p.setPen(QPen(line, 2))
        p.drawPath(path)
        p.setBrush(line)
        for t, r in pts:
            p.drawEllipse(QPointF(left + (t - start) / self.window * w, y_of(r)), 2.5, 2.5)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1040, 700)
        self.setMinimumSize(760, 520)

        self.settings = QSettings(str(data_dir() / "settings.ini"), QSettings.IniFormat)
        self.tracker = DeviceTracker(max_age=60.0)
        self.worker: ScanWorker | None = None
        self.selected_address: str | None = None

        self._build_ui()
        self._load_settings()

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.scan_btn = QPushButton("Start scan")
        self.scan_btn.setAccessibleName("Start or stop scanning")
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name or address")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setAccessibleName("Filter")
        self.min_rssi = QSpinBox()
        self.min_rssi.setRange(-100, 0)
        self.min_rssi.setValue(-100)
        self.min_rssi.setPrefix("Min ")
        self.min_rssi.setSuffix(" dBm")
        self.min_rssi.setAccessibleName("Minimum RSSI")
        self.hide_unnamed = QCheckBox("Hide unnamed")
        self.export_btn = QPushButton("Export CSV…")
        self.clear_btn = QPushButton("Clear")

        top = QHBoxLayout()
        top.addWidget(self.scan_btn)
        top.addWidget(self.filter_edit, 1)
        top.addWidget(self.min_rssi)
        top.addWidget(self.hide_unnamed)
        top.addWidget(self.export_btn)
        top.addWidget(self.clear_btn)

        self.tx_power = QSpinBox()
        self.tx_power.setRange(-100, -30)
        self.tx_power.setValue(DEFAULT_TX_POWER_1M)
        self.tx_power.setSuffix(" dBm")
        self.tx_power.setAccessibleName("RSSI at 1 metre")
        self.tx_power.setToolTip("Measured RSSI when the device is exactly 1 m away. Calibrate per device for better estimates.")
        self.path_loss = QDoubleSpinBox()
        self.path_loss.setRange(1.5, 5.0)
        self.path_loss.setSingleStep(0.1)
        self.path_loss.setValue(DEFAULT_PATH_LOSS)
        self.path_loss.setAccessibleName("Path-loss exponent")
        self.path_loss.setToolTip("2.0 = open space. Indoors with walls: 2.5 - 4.0.")
        calib = QHBoxLayout()
        calib.addWidget(QLabel("Distance model (approximate):"))
        calib.addWidget(QLabel("RSSI at 1 m"))
        calib.addWidget(self.tx_power)
        calib.addWidget(QLabel("Path-loss exponent"))
        calib.addWidget(self.path_loss)
        calib.addStretch(1)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAccessibleName("Nearby Bluetooth devices")
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)

        self.readout = QLabel("No device selected")
        big = QFont(self.font())
        big.setPointSizeF(big.pointSizeF() + 4)
        big.setBold(True)
        self.readout.setFont(big)
        self.readout.setAccessibleName("Selected device readout")
        self.graph = SignalGraph()

        root = QVBoxLayout()
        root.addLayout(top)
        root.addLayout(calib)
        root.addWidget(self.table, 3)
        root.addWidget(self.readout)
        root.addWidget(self.graph, 2)
        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("&About", self.show_about)

        self.scan_btn.clicked.connect(self.toggle_scan)
        self.export_btn.clicked.connect(self.export)
        self.clear_btn.clicked.connect(self.clear)
        self.table.itemSelectionChanged.connect(self._on_selection)
        for w in (self.filter_edit,):
            w.textChanged.connect(self.refresh)
        self.min_rssi.valueChanged.connect(self.refresh)
        self.hide_unnamed.toggled.connect(self.refresh)
        self.tx_power.valueChanged.connect(self.refresh)
        self.path_loss.valueChanged.connect(self.refresh)

        self.setTabOrder(self.scan_btn, self.filter_edit)
        self.statusBar().showMessage("Ready. Click Start scan.")

    def _load_settings(self) -> None:
        self.min_rssi.setValue(int(self.settings.value("min_rssi", -100)))
        self.tx_power.setValue(int(self.settings.value("tx_power", DEFAULT_TX_POWER_1M)))
        self.path_loss.setValue(float(self.settings.value("path_loss", DEFAULT_PATH_LOSS)))
        self.hide_unnamed.setChecked(str(self.settings.value("hide_unnamed", "false")).lower() == "true")

    def _save_settings(self) -> None:
        self.settings.setValue("min_rssi", self.min_rssi.value())
        self.settings.setValue("tx_power", self.tx_power.value())
        self.settings.setValue("path_loss", self.path_loss.value())
        self.settings.setValue("hide_unnamed", self.hide_unnamed.isChecked())

    # ------------------------------------------------------------ scanning
    def toggle_scan(self) -> None:
        if self.worker is None:
            self.start_scan()
        else:
            self.stop_scan()

    def start_scan(self) -> None:
        self.worker = ScanWorker(self)
        self.worker.advertisement.connect(self.on_advertisement)
        self.worker.failed.connect(self.on_scan_failed)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()
        self.scan_btn.setText("Stop scan")
        self.statusBar().showMessage("Scanning for Bluetooth LE devices…")

    def stop_scan(self) -> None:
        if self.worker is not None:
            self.scan_btn.setEnabled(False)
            self.worker.request_stop()

    def _on_worker_finished(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None
        self.scan_btn.setText("Start scan")
        self.scan_btn.setEnabled(True)
        self.statusBar().showMessage("Scan stopped.")

    def on_scan_failed(self, message: str) -> None:
        QMessageBox.warning(self, APP_NAME, message)

    def on_advertisement(self, address: str, name: str, rssi: int) -> None:
        self.tracker.update(address, name, rssi)

    # ------------------------------------------------------------- refresh
    def _visible_devices(self) -> list[DeviceStats]:
        return self.tracker.snapshot(
            min_rssi=self.min_rssi.value(),
            text=self.filter_edit.text(),
            hide_unnamed=self.hide_unnamed.isChecked(),
        )

    def refresh(self) -> None:
        now = time.time()
        self.tracker.prune(now)
        devices = self._visible_devices()
        tx, n = self.tx_power.value(), self.path_loss.value()

        self.table.blockSignals(True)
        self.table.setRowCount(len(devices))
        selected_row = -1
        for row, d in enumerate(devices):
            label, _bars = quality(d.avg)
            age = now - d.last_seen
            cells = [
                d.display_name, d.address, str(d.rssi), f"{d.avg:.1f}", label,
                format_distance(estimate_distance_m(d.avg, tx, n)), str(d.samples),
                "now" if age < 1 else f"{age:.0f} s ago",
            ]
            for col, text in enumerate(cells):
                item = self.table.item(row, col) or QTableWidgetItem()
                item.setText(text)
                if col in (2, 3, 6):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 4:
                    item.setForeground(QColor(QUALITY_COLORS[label]))
                self.table.setItem(row, col, item)
            if d.address == self.selected_address:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        else:
            self.table.clearSelection()
        self.table.blockSignals(False)

        self._update_readout(now, tx, n)
        total = len(self.tracker)
        scanning = "Scanning" if self.worker is not None else "Idle"
        self.statusBar().showMessage(f"{scanning}: {len(devices)} shown of {total} device(s) seen in the last 60 s")

    def _update_readout(self, now: float, tx: float, n: float) -> None:
        dev = self.tracker.get(self.selected_address) if self.selected_address else None
        if dev is None:
            self.readout.setText("No device selected")
            self.graph.set_data([], now)
            return
        label, _ = quality(dev.avg)
        dist = format_distance(estimate_distance_m(dev.avg, tx, n))
        self.readout.setText(
            f"{dev.display_name}:  {dev.rssi} dBm  (avg {dev.avg:.1f}, min {dev.min_rssi}, max {dev.max_rssi})  -  {label}  -  {dist}"
        )
        self.graph.set_data(dev.history, now)

    def _on_selection(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.selected_address = self.table.item(rows[0].row(), 1).text()
        else:
            self.selected_address = None
        self._update_readout(time.time(), self.tx_power.value(), self.path_loss.value())

    # ------------------------------------------------------------- actions
    def export(self) -> None:
        devices = self._visible_devices()
        if not devices:
            QMessageBox.information(self, APP_NAME, "There are no devices to export yet.")
            return
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        default = str(Path(docs) / f"bluetooth-signal-{time.strftime('%Y%m%d-%H%M%S')}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", default, "CSV files (*.csv)")
        if not path:
            return
        try:
            count = export_csv(path, devices, self.tx_power.value(), self.path_loss.value())
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not write the file:\n{exc}")
            return
        self.statusBar().showMessage(f"Exported {count} device(s) to {path}", 8000)

    def clear(self) -> None:
        self.tracker.clear()
        self.selected_address = None
        self.refresh()

    def show_about(self) -> None:
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "Shows the received signal strength (RSSI) of nearby Bluetooth Low Energy devices.<br><br>"
            "RSSI is only reported for devices that are advertising. Classic Bluetooth devices "
            "(many older headsets and speakers) are not listed. Distance values are rough estimates.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._save_settings()
        if self.worker is not None:
            self.worker.request_stop()
            self.worker.wait(3000)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    setup_logging()
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
