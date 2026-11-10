"""Signal-strength maths and device tracking. No Qt, no Bluetooth imports."""
from __future__ import annotations

import csv
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Deque, Iterable, List, Optional, Tuple

DEFAULT_TX_POWER_1M = -59      # typical BLE RSSI measured at 1 metre (dBm)
DEFAULT_PATH_LOSS = 2.0        # free space ~2.0; indoors usually 2.5-4.0
HISTORY_SECONDS = 120.0        # how much history each device keeps
EMA_ALPHA = 0.3                # smoothing factor for the average RSSI

# (minimum RSSI, label, bars out of 4)
QUALITY_LEVELS = [
    (-50, "Excellent", 4),
    (-60, "Good", 3),
    (-70, "Fair", 2),
    (-80, "Weak", 1),
]


def quality(rssi: float) -> Tuple[str, int]:
    """Map an RSSI in dBm to a (label, bars) pair."""
    for threshold, label, bars in QUALITY_LEVELS:
        if rssi >= threshold:
            return label, bars
    return "Very weak", 0


def estimate_distance_m(
    rssi: float,
    tx_power_1m: float = DEFAULT_TX_POWER_1M,
    path_loss: float = DEFAULT_PATH_LOSS,
) -> float:
    """Log-distance path-loss estimate. Rough: walls, bodies and antennas
    change RSSI by 10-20 dB, so treat the result as an order of magnitude."""
    if path_loss <= 0:
        raise ValueError("path_loss must be > 0")
    return 10 ** ((tx_power_1m - rssi) / (10 * path_loss))


def ema(previous: Optional[float], new: float, alpha: float = EMA_ALPHA) -> float:
    """Exponential moving average; the first sample seeds the average."""
    if previous is None:
        return float(new)
    return alpha * new + (1 - alpha) * previous


@dataclass
class DeviceStats:
    address: str
    name: str = ""
    rssi: int = 0
    avg: float = 0.0
    min_rssi: int = 0
    max_rssi: int = 0
    samples: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    history: Deque[Tuple[float, int]] = field(default_factory=deque)

    @property
    def display_name(self) -> str:
        return self.name or "(unnamed)"


class DeviceTracker:
    """Keeps per-device RSSI statistics from a stream of advertisements."""

    def __init__(self, max_age: float = 60.0, clock: Callable[[], float] = time.time):
        self.max_age = max_age
        self._clock = clock
        self._devices: dict[str, DeviceStats] = {}

    def __len__(self) -> int:
        return len(self._devices)

    def get(self, address: str) -> Optional[DeviceStats]:
        return self._devices.get(address)

    def clear(self) -> None:
        self._devices.clear()

    def update(self, address: str, name: str, rssi: int, now: Optional[float] = None) -> DeviceStats:
        now = self._clock() if now is None else now
        dev = self._devices.get(address)
        if dev is None:
            dev = DeviceStats(address=address, name=name or "", rssi=rssi, avg=float(rssi),
                              min_rssi=rssi, max_rssi=rssi, first_seen=now)
            self._devices[address] = dev
        else:
            if name:  # advertisements often omit the name; keep the last known one
                dev.name = name
            dev.avg = ema(dev.avg, rssi)
            dev.min_rssi = min(dev.min_rssi, rssi)
            dev.max_rssi = max(dev.max_rssi, rssi)
        dev.rssi = rssi
        dev.samples += 1
        dev.last_seen = now
        dev.history.append((now, rssi))
        cutoff = now - HISTORY_SECONDS
        while dev.history and dev.history[0][0] < cutoff:
            dev.history.popleft()
        return dev

    def prune(self, now: Optional[float] = None) -> int:
        """Forget devices not heard from for max_age seconds. Returns count removed."""
        now = self._clock() if now is None else now
        stale = [a for a, d in self._devices.items() if now - d.last_seen > self.max_age]
        for a in stale:
            del self._devices[a]
        return len(stale)

    def snapshot(
        self,
        min_rssi: Optional[float] = None,
        text: str = "",
        hide_unnamed: bool = False,
    ) -> List[DeviceStats]:
        """Devices sorted strongest-first, optionally filtered."""
        text = text.strip().lower()
        out = []
        for d in self._devices.values():
            if hide_unnamed and not d.name:
                continue
            if min_rssi is not None and d.avg < min_rssi:
                continue
            if text and text not in d.name.lower() and text not in d.address.lower():
                continue
            out.append(d)
        out.sort(key=lambda d: (-d.avg, d.display_name.lower(), d.address))
        return out


CSV_HEADER = ["timestamp", "name", "address", "rssi_dbm", "avg_rssi_dbm",
              "min_rssi_dbm", "max_rssi_dbm", "quality", "est_distance_m", "samples"]


def export_csv(
    path: Path | str,
    devices: Iterable[DeviceStats],
    tx_power_1m: float = DEFAULT_TX_POWER_1M,
    path_loss: float = DEFAULT_PATH_LOSS,
    now: Optional[float] = None,
) -> int:
    """Write a snapshot of devices to CSV. Returns number of rows written."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now if now is not None else time.time()))
    rows = 0
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # BOM: opens cleanly in Excel
        writer = csv.writer(fh)
        writer.writerow(CSV_HEADER)
        for d in devices:
            label, _ = quality(d.avg)
            writer.writerow([
                stamp, d.name, d.address, d.rssi, f"{d.avg:.1f}", d.min_rssi, d.max_rssi,
                label, f"{estimate_distance_m(d.avg, tx_power_1m, path_loss):.2f}", d.samples,
            ])
            rows += 1
    return rows
