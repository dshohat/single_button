# Emergency Alert System — Arduino/PlatformIO

Real-time emergency alert system for ESP32 that connects to the Israeli Home Front Command (Pikud HaOref) Red Alert API. Displays alerts on an OLED screen with Hebrew text, sounds an audible alarm, and provides a web-based configuration UI.

## Features

- **Real-time alerts** — polls Pikud HaOref every 3 seconds over persistent TLS
- **State machine** — IDLE → WARNING (התרעה) → SHELTER (למקלט!) → CLEAR (לצאת) → IDLE
- **Big Hebrew text on OLED** — full-screen pre-rendered bitmaps for each alert state
- **Mario-style alarm** — audible "E-LA-mama" tune on SHELTER alerts (plays once, interruptible by button)
- **WARNING = flash only** — OLED flashes the warning bitmap, no sound
- **CLEAR auto-expires** — returns to IDLE after 60 seconds
- **Web UI** — configure WiFi, select monitored areas/cities, view alert log, run tests
- **Test injection** — send test alerts from your laptop using `test_alerts.py`
- **Night mode** — turn off the display via web UI
- **Factory reset** — hold button 3 seconds on boot

## Hardware

| Component | Pin |
|-----------|-----|
| SSD1306 128×64 OLED (SDA) | GPIO 21 |
| SSD1306 128×64 OLED (SCL) | GPIO 22 |
| Buzzer (PWM) | GPIO 23 |
| Button (INPUT_PULLUP) | GPIO 4 |

## Installation from VS Code with PlatformIO

### Prerequisites

1. **VS Code** — install from https://code.visualstudio.com/
2. **PlatformIO IDE extension** — install from the VS Code Extensions marketplace:
   - Open VS Code
   - Press `Ctrl+Shift+X` to open Extensions
   - Search for **"PlatformIO IDE"**
   - Click **Install**
   - Wait for PlatformIO to finish its initial setup (this downloads toolchains and may take a few minutes on first install)
3. **CP210x USB driver** — if your ESP32 uses a CP2102/CP2104 USB-to-UART chip, install the Silicon Labs driver:
   - Download from https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers
   - Or use the copy in `installation_files/` at the project root
   - After installing, the device should appear in Device Manager under **Ports (COM & LPT)** as `Silicon Labs CP210x USB to UART Bridge (COMx)`

### Step-by-Step Setup

#### 1. Open the project

- In VS Code, go to **File → Open Folder**
- Navigate to and select the `mine/arduino` folder
- PlatformIO will automatically detect `platformio.ini` and configure the project

#### 2. Set your COM port

- Open `platformio.ini`
- Change `upload_port` and `monitor_port` to match your ESP32's COM port:
  ```ini
  upload_port = COM5      ; ← change to your port
  monitor_port = COM5     ; ← change to your port
  ```
- To find your port: open Device Manager → Ports (COM & LPT) → look for the Silicon Labs entry

#### 3. Upload the area data files

The ESP32 needs area/city data on its LittleFS filesystem. Upload it once:

- Open a PlatformIO terminal: click the PlatformIO icon in the left sidebar → **Project Tasks** → **esp32dev** → **Platform** → **Upload Filesystem Image**
- Or run from the terminal:
  ```
  pio run --target uploadfs
  ```
- This uploads the contents of the `data/` folder (`areas.json`, `area_names.json`) to the ESP32's flash

#### 4. Build and upload the firmware

- Click the **PlatformIO: Build** button (✓ checkmark) in the bottom status bar
- Or press `Ctrl+Alt+B`
- Or run from the terminal:
  ```
  pio run
  ```
- Once it compiles successfully, upload:
  - Click the **PlatformIO: Upload** button (→ arrow) in the bottom status bar
  - Or press `Ctrl+Alt+U`
  - Or run:
    ```
    pio run --target upload
    ```

#### 5. Monitor serial output

- Click the **PlatformIO: Serial Monitor** button (plug icon) in the status bar
- Or run:
  ```
  pio device monitor
  ```
- You should see boot messages, WiFi connection status, and alert polling logs

### First Boot — WiFi Setup

1. On first boot (or after factory reset), the ESP32 starts in **AP mode**
2. Connect your phone/laptop to WiFi network **`ESP32-Alert`** (password: `12345678`)
3. Open a browser and go to **http://192.168.4.1**
4. Select your WiFi network, enter the password, and click **Connect & Reboot**
5. The device will restart and connect to your WiFi

### Configuring Monitored Areas

1. Find the device's IP address (shown on the OLED screen after connecting)
2. Open `http://<device-ip>/areas` in your browser
3. Select the areas and cities you want to monitor
4. Click **Save**

## Testing

### Quick Test (Web UI)

1. Go to `http://<device-ip>/test_page`
2. Use the dropdown to select Warning / Shelter / Clear
3. Click **Quick Test**

### Inject Test (from laptop)

1. On the test page, click **Enter Inject Mode**
2. On your laptop, run:
   ```
   cd mine/test
   python test_alerts.py <device-ip>
   ```
3. Use the interactive menu to send realistic alert payloads
4. When done, click **End Inject Mode** on the test page

## Project Structure

```
arduino/
├── platformio.ini          # PlatformIO build configuration
├── data/
│   ├── areas.json          # Master list of areas → cities
│   └── area_names.json     # Area name list
├── src/
│   ├── main.cpp            # Main firmware source
│   └── hebrew_bitmaps.h    # Pre-rendered Hebrew XBM bitmaps for OLED
└── lib/
    └── U8g2/               # OLED display library (local copy)
```

## LittleFS Files (on-device)

| File | Description |
|------|-------------|
| `/wifi_config.json` | Saved WiFi networks |
| `/alert_areas.json` | Selected areas/cities to monitor |
| `/areas.json` | Master area→cities reference (uploaded from `data/`) |
| `/area_names.json` | Area name list (uploaded from `data/`) |
| `/alert_log.txt` | Alert history log |
