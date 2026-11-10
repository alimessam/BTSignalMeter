# BT Signal Meter

Windows desktop app that scans for nearby **Bluetooth Low Energy** devices and shows their
signal strength (RSSI) live: sortable device table, signal-quality rating, rough distance
estimate, a rolling 60-second graph for the selected device, filters and CSV export.

Built with Python, PySide6 (Qt) and [bleak](https://github.com/hbldh/bleak) (uses the Windows WinRT Bluetooth APIs).

## Prerequisites
- Windows 10 22H2 / Windows 11 with a Bluetooth LE adapter, Bluetooth turned **on**
- Python 3.10+ (64-bit): `winget install Python.Python.3.12`
- Optional: Inno Setup for the installer: `winget install JRSoftware.InnoSetup`
- No administrator rights needed.

## Run (development)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app\main.py
```
Click **Start scan**. Select a row to see its history graph.

## Tests (any OS)
```powershell
pip install pytest
python -m pytest
```

## Build a distributable
```powershell
.\build.ps1
```
Produces `dist\BTSignalMeter.exe` (folder build) and, if Inno Setup is installed,
`Output\bt-signal-meter-setup-1.0.0.exe` (per-user install, no admin prompt).

## Using the readings
| Column | Meaning |
|---|---|
| RSSI | Last packet's signal strength in dBm (closer to 0 = stronger) |
| Avg | Smoothed value (exponential moving average) - use this for decisions |
| Quality | Excellent >= -50, Good >= -60, Fair >= -70, Weak >= -80, else Very weak |
| Est. distance | Log-distance model. Set *RSSI at 1 m* by measuring your device at 1 metre, and raise the path-loss exponent (2.5-4) indoors. Treat as rough. |

## Limitations
- Only **advertising BLE devices** report RSSI. Classic Bluetooth devices (many older headsets/speakers) and devices that are already connected exclusively to another host may not appear.
- Phones and many trackers use **randomized addresses**, so one device can show up as several rows over time.
- RSSI varies 5-10 dB from body position, antenna orientation and walls; distance is an estimate, not a measurement.

## Where data goes
Settings and log: `%LOCALAPPDATA%\BTSignalMeter\` (`settings.ini`, `app.log`)

## Troubleshooting
- *"Could not scan for Bluetooth devices"*: turn Bluetooth on in Windows Settings and check Device Manager shows a working adapter.
- Built exe starts and immediately fails with a `winrt` import error: rebuild with `--collect-all winrt` added in `build.ps1`.
- Unsigned executables may trigger SmartScreen/antivirus warnings. Sign with `signtool` for distribution.
