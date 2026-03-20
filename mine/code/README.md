# MicroPython Code for Single Button ESP32

This folder contains my MicroPython additions to the **Single Button ESP32** project.

## What's Here

| File | Description |
|------|-------------|
| `emergency_alerts.py` | Emergency alert system — connects to Pikud HaOref and monitors real-time alerts |
| `emergency_alerts.mpy` | Pre-compiled version of the above (for faster loading on ESP32) |
| `boot.py` | Boot script |
| `ssd1306.py` | SSD1306 OLED driver |
| `font.py` | Font rendering utilities |
| `singame.py` | Single-button game |
| `dino.py` | Dino game |
| `mario.py` | Mario game |
| `170 space animated.py` | Space animated game |
| `generate_bitmaps.py` | Script to generate Hebrew text bitmaps for OLED display |
| `areas.json` / `area_names.json` / `alert_areas.json` | Alert area configuration data |
| `wifi_config.json` | WiFi network configuration |
| `ASC16` / `ASC24` / `ASC32` | Font bitmap files |

## How to Use

This works like any other MicroPython project on the Single Button ESP32 kit:

1. Follow the setup steps in the [main project README](../../README.md) (steps 1–8) to get Thonny set up with MicroPython on your ESP32
2. Connect the ESP32 via USB
3. Open Thonny and connect to the board
4. Upload the files from this folder to the ESP32's filesystem
5. Run the desired script (e.g., `emergency_alerts.py`)

> **Note:** The emergency alerts project has moved to Arduino/PlatformIO for better performance and TLS support. See the [Arduino version](../arduino/README.md) for the current active implementation.

## Hardware

- **Board:** ESP32-WROOM (Single Button kit)
- **Display:** SSD1306 128×64 OLED (I2C, SDA=21, SCL=22)
- **Buzzer:** PWM on pin 23
- **Button:** Pin 4 (INPUT_PULLUP)
