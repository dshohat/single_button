# My Projects — Single Button ESP32

Custom projects built on top of the [Single Button ESP32](../README.md) kit (ESP32-WROOM + SSD1306 OLED + buzzer + button).

## Folder Overview

| Folder | Description |
|--------|-------------|
| [**arduino/**](arduino/README.md) | **Emergency Alert System** — Arduino/PlatformIO firmware that connects to the Pikud HaOref Red Alert API, displays Hebrew alerts on the OLED, and sounds alarms. This is the main active project. |
| [**code/**](code/README.md) | **MicroPython scripts** — Games and utilities written in MicroPython for the Single Button kit, including an earlier MicroPython version of the emergency alerts system. |
| **test/** | Test tools — `test_alerts.py` for injecting test alerts to the ESP32, and `alert.log` with captured real alert data. |
| **misc/** | Miscellaneous — MicroPython firmware binary, TLS test scripts, and setup notes. |

## Quick Start

- **Emergency Alerts (Arduino):** See [arduino/README.md](arduino/README.md) for full installation instructions using VS Code + PlatformIO
- **MicroPython Games:** See [code/README.md](code/README.md) for setup using Thonny
