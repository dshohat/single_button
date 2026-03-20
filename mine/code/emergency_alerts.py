# ****************************************
# Emergency Alert System for ESP32
# Connects to Israeli Home Front Command (Pikud HaOref) Red Alert system
#
# Config files:
#   wifi_config.json  - saved WiFi networks [{ssid, password}, ...]
#   alert_areas.json  - monitored areas/cities {area: [city,...] or "ALL"}
#   areas.json        - master list of all areas->cities (read-only reference)
#
# Flow:
# 1. No saved WiFi: starts AP "ESP32-Alert" (pass: 12345678)
#    -> browse 192.168.4.1, pick network, enter password, saves & reboots
# 2. Tries each saved network until one connects
# 3. Browse device IP for config pages (areas, wifi management, test)
# 4. Hold button 3s on boot to clear all config
# ****************************************

from machine import Pin, I2C, PWM, reset
import ssd1306
import framebuf
import network
import socket
import json
import time
import gc

# --- Hardware Setup ---
time.sleep(2)
i2c = I2C(scl=Pin(22), sda=Pin(21), freq=400000)
display = ssd1306.SSD1306_I2C(128, 64, i2c)
button = Pin(4, Pin.IN, Pin.PULL_UP)
buzzer_pwm = PWM(Pin(23))
buzzer_pwm.duty(0)

# --- Constants ---
WIFI_FILE = 'wifi_config.json'
AREAS_CFG_FILE = 'alert_areas.json'
AREAS_REF_FILE = 'areas.json'
AREA_NAMES_FILE = 'area_names.json'
AP_SSID = 'ESP32-Alert'
AP_PASSWORD = '12345678'
ALERT_HOST = 'www.oref.org.il'
ALERT_REQUEST = (b'GET /WarningMessages/alert/alerts.json HTTP/1.1\r\n'
    b'Host: www.oref.org.il\r\n'
    b'User-Agent: Mozilla/5.0\r\n'
    b'Referer: https://www.oref.org.il/\r\n'
    b'X-Requested-With: XMLHttpRequest\r\n'
    b'Connection: keep-alive\r\n\r\n')
POLL_INTERVAL_MS = 3000

_test_alert = None
_cached_networks = []
_monitored_cities = set()
_alert_conn = None

# --- Alert State Machine ---
STATE_IDLE = 0
STATE_WARNING = 1   # בדקות הקרובות -> display התרעה
STATE_SHELTER = 2   # ירי רקטות  -> display למקלט
STATE_CLEAR = 3     # האירוע הסתיים -> display לצאת

STATE_LABELS = {STATE_IDLE: 'Idle', STATE_WARNING: 'Warning', STATE_SHELTER: 'Shelter!', STATE_CLEAR: 'Clear'}
STATE_HEBREW = {STATE_IDLE: '', STATE_WARNING: '\u05d4\u05ea\u05e8\u05e2\u05d4', STATE_SHELTER: '\u05dc\u05de\u05e7\u05dc\u05d8!', STATE_CLEAR: '\u05dc\u05e6\u05d0\u05ea'}
STATE_BITMAPS = {STATE_WARNING: 'alert_warn.bin', STATE_SHELTER: 'alert_shelter.bin', STATE_CLEAR: 'alert_clear.bin'}

_alert_state = STATE_IDLE

def classify_alert(title):
    """Map alert title to a state. Returns None if unrecognized."""
    if '\u05d9\u05e8\u05d9 \u05e8\u05e7\u05d8\u05d5\u05ea' in title or '\u05d7\u05d3\u05d9\u05e8\u05ea' in title or '\u05e8\u05e2\u05d9\u05d3\u05ea' in title:
        return STATE_SHELTER
    if '\u05d1\u05d3\u05e7\u05d5\u05ea \u05d4\u05e7\u05e8\u05d5\u05d1\u05d5\u05ea' in title or '\u05d4\u05ea\u05e8\u05e2\u05d4 \u05de\u05d5\u05e7\u05d3\u05de\u05ea' in title:
        return STATE_WARNING
    if '\u05d4\u05d0\u05d9\u05e8\u05d5\u05e2 \u05d4\u05e1\u05ea\u05d9\u05d9\u05dd' in title:
        return STATE_CLEAR
    return None

def show_state_bitmap(state):
    """Display full-screen Hebrew bitmap for the given state."""
    fn = STATE_BITMAPS.get(state)
    if not fn:
        return
    try:
        with open(fn, 'rb') as f:
            data = bytearray(f.read())
        fb = framebuf.FrameBuffer(data, 128, 64, framebuf.MONO_VLSB)
        display.fill(0)
        display.blit(fb, 0, 0)
        display.show()
    except Exception as e:
        print('Bitmap error:', e)

# --- Utility ---

def buzz(ms, freq):
    if freq > 0:
        buzzer_pwm.freq(freq)
        buzzer_pwm.duty(512)
    time.sleep_ms(ms)
    buzzer_pwm.duty(0)

def show(lines):
    display.fill(0)
    for i, line in enumerate(lines):
        if i > 5:
            break
        display.text(str(line)[:16], 0, i * 10)
    display.show()

def siren():
    """Play one siren sweep. Returns True if button was pressed during play."""
    for f in range(400, 900, 30):
        if not button.value():
            buzzer_pwm.duty(0)
            return True
        buzzer_pwm.freq(f)
        buzzer_pwm.duty(512)
        time.sleep_ms(12)
    for f in range(900, 400, -30):
        if not button.value():
            buzzer_pwm.duty(0)
            return True
        buzzer_pwm.freq(f)
        buzzer_pwm.duty(512)
        time.sleep_ms(12)
    buzzer_pwm.duty(0)
    return False

def startup_beep():
    buzz(100, 660)
    time.sleep_ms(50)
    buzz(100, 880)

def url_decode(s):
    s = s.replace('+', ' ')
    res = bytearray()
    i = 0
    while i < len(s):
        if s[i] == '%' and i + 2 < len(s):
            try:
                res.append(int(s[i+1:i+3], 16))
                i += 3
            except ValueError:
                res.append(ord(s[i]))
                i += 1
        else:
            res.append(ord(s[i]))
            i += 1
    return res.decode('utf-8', 'ignore')

def parse_qs_multi(body):
    """Parse form body, returns dict where each key maps to a list of values."""
    params = {}
    for pair in body.split('&'):
        if '=' in pair:
            k, v = pair.split('=', 1)
            key = url_decode(k)
            val = url_decode(v)
            if key not in params:
                params[key] = []
            params[key].append(val)
    return params

# --- Config: WiFi networks ---

def load_wifi():
    try:
        with open(WIFI_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_wifi(networks):
    with open(WIFI_FILE, 'w') as f:
        json.dump(networks, f)

# --- Config: Alert areas ---

def load_alert_cfg():
    """Returns dict: {area_name: [city1,...] or "ALL"}"""
    try:
        with open(AREAS_CFG_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_alert_cfg(cfg):
    with open(AREAS_CFG_FILE, 'w') as f:
        json.dump(cfg, f)

# --- Reference: all areas/cities ---

def load_ref_areas():
    """Returns dict: {area_name: [city1, city2, ...]}"""
    try:
        with open(AREAS_REF_FILE, 'r') as f:
            data = json.load(f)
        return data.get('areas', {})
    except:
        return {}

def refresh_monitored_cache():
    """Recompute monitored cities from config. Call after area config changes."""
    global _monitored_cities
    alert_cfg = load_alert_cfg()
    if not alert_cfg:
        _monitored_cities = set()
        return
    ref = load_ref_areas()
    cities = set()
    for area, selection in alert_cfg.items():
        if area not in ref:
            continue
        if selection == "ALL":
            for c in ref[area]:
                cities.add(c)
        elif isinstance(selection, list):
            for c in selection:
                cities.add(c)
    del ref
    gc.collect()
    _monitored_cities = cities

def clear_all_config():
    import os
    for f in [WIFI_FILE, AREAS_CFG_FILE]:
        try:
            os.remove(f)
        except:
            pass

# --- WiFi ---

def scan_networks():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    time.sleep(1)
    nets = sta.scan()
    seen = {}
    for net in nets:
        ssid = net[0].decode('utf-8', 'ignore')
        rssi = net[3]
        if ssid and (ssid not in seen or seen[ssid] < rssi):
            seen[ssid] = rssi
    sta.active(False)
    return sorted(seen.items(), key=lambda x: x[1], reverse=True)

def connect_wifi_multi(networks, timeout=15):
    """Try connecting to each saved network. Returns (ssid, ip) or (None, None)."""
    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    for net in networks:
        ssid = net.get('ssid', '')
        pwd = net.get('password', '')
        if not ssid:
            continue
        show(['Trying:', ssid[:16], '...'])
        if sta.isconnected():
            sta.disconnect()
        sta.connect(ssid, pwd)
        start = time.time()
        while not sta.isconnected():
            if time.time() - start > timeout:
                break
            time.sleep(0.5)
        if sta.isconnected():
            return ssid, sta.ifconfig()[0]
    return None, None

def start_ap():
    sta = network.WLAN(network.STA_IF)
    sta.active(False)
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, password=AP_PASSWORD, authmode=3)
    while not ap.active():
        time.sleep(0.1)
    return ap.ifconfig()[0]

# --- HTML ---

STYLE = ('<style>'
    'body{font-family:sans-serif;margin:20px;direction:rtl;background:#1a1a2e;color:#eee}'
    'h1,h2{color:#e94560}'
    'input,select,textarea,button{font-size:16px;padding:8px;margin:5px 0;box-sizing:border-box;direction:rtl}'
    'button{background:#e94560;color:#fff;border:none;border-radius:4px;cursor:pointer;width:100%}'
    'button:hover{background:#c73e54}'
    '.card{background:#16213e;padding:15px;border-radius:8px;margin:10px 0}'
    'a{color:#e94560}'
    'nav{margin:10px 0}nav a{margin-left:15px;font-size:15px}'
    'label.item{display:block;padding:6px 4px;font-size:15px;cursor:pointer}'
    'label.item:hover{background:#1f2b4d;border-radius:4px}'
    'input[type=checkbox]{width:18px;height:18px;margin-left:8px}'
    '.del-btn{background:#c0392b;width:auto;padding:4px 12px;font-size:13px;margin-right:8px}'
    '.test-btn{background:#ff9800}.test-btn:hover{background:#e68a00}'
    '.scroll{max-height:300px;overflow-y:auto;background:#0f1a30;padding:8px;border-radius:4px}'
    '.state-box{text-align:center;padding:20px;border-radius:8px;margin:10px 0}'
    '.state-idle{background:#16213e;border:2px solid #444}'
    '.state-warn{background:#3d2100;border:3px solid #ff9800}'
    '.state-shelter{background:#3d0000;border:3px solid #e94560}'
    '.state-clear{background:#003d10;border:3px solid #2ecc71}'
    '</style>')

def nav_bar():
    return '<nav><a href="/">Home</a><a href="/wifi">WiFi</a><a href="/areas">Areas</a><a href="/test_page">Test</a></nav><hr>'

def _state_html():
    """Return HTML fragment showing current alert state."""
    st = _alert_state
    if st == STATE_SHELTER:
        css = 'state-shelter'
        heb = '\u05dc\u05de\u05e7\u05dc\u05d8!'
        lbl = 'SHELTER'
    elif st == STATE_WARNING:
        css = 'state-warn'
        heb = '\u05d4\u05ea\u05e8\u05e2\u05d4'
        lbl = 'WARNING'
    elif st == STATE_CLEAR:
        css = 'state-clear'
        heb = '\u05dc\u05e6\u05d0\u05ea'
        lbl = 'CLEAR'
    else:
        css = 'state-idle'
        heb = '\u05ea\u05e7\u05d9\u05df'
        lbl = 'IDLE'
    return ('<div class="state-box ' + css + '">'
        '<h1 style="font-size:64px;margin:0">' + heb + '</h1>'
        '<p style="font-size:18px;margin:5px 0">' + lbl + '</p></div>')

def page_home(connected_ssid):
    alert_cfg = load_alert_cfg()
    n = len(alert_cfg)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="refresh" content="5">'
        '<title>Emergency Alerts</title>' + STYLE + '</head><body>'
        '<h1>Emergency Alerts</h1>' + nav_bar() +
        _state_html() +
        '<div class="card"><h2>Status</h2>'
        '<p>Connected to: <b>' + connected_ssid + '</b></p>'
        '<p>Monitoring: <b>' + str(n) + ' area(s)</b></p></div>'
        '</body></html>'
    )

# --- WiFi management page ---

def page_wifi_manage():
    networks = load_wifi()
    rows = ''
    for i, net in enumerate(networks):
        rows += ('<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0">'
                '<span>' + net.get('ssid', '?') + '</span>'
                '<form method="POST" action="/wifi_del" style="margin:0">'
                '<input type="hidden" name="idx" value="' + str(i) + '">'
                '<button class="del-btn" type="submit">Delete</button>'
                '</form></div>')
    if not rows:
        rows = '<p>No saved networks.</p>'
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>WiFi</title>' + STYLE + '</head><body>'
        '<h1>WiFi Networks</h1>' + nav_bar() +
        '<div class="card"><h2>Saved Networks</h2>' + rows +
        '<form method="POST" action="/wifi_del_all" style="margin-top:10px">'
        '<button style="background:#555" type="submit">Delete All Networks</button>'
        '</form></div>'
        '<div class="card"><h2>Add Network</h2>'
        '<form method="POST" action="/wifi_add">'
        '<label>SSID:</label><input type="text" name="ssid" style="width:100%"><br>'
        '<label>Password:</label><input type="password" name="password" style="width:100%"><br><br>'
        '<button type="submit">Add Network</button>'
        '</form></div>'
        '</body></html>'
    )

# --- WiFi setup page (AP mode) ---

def page_wifi_setup(scan_results):
    opts = ''
    for ssid, rssi in scan_results:
        opts += '<option value="' + ssid + '">' + ssid + ' (' + str(rssi) + 'dBm)</option>'
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>WiFi Setup</title>' + STYLE + '</head><body>'
        '<h1>ESP32 Alert - WiFi Setup</h1>'
        '<div class="card">'
        '<form method="POST" action="/save_wifi">'
        '<label>Select Network:</label>'
        '<select name="ssid">' + opts + '</select><br><br>'
        '<label>Password:</label>'
        '<input type="password" name="password"><br><br>'
        '<button type="submit">Connect & Reboot</button>'
        '</form></div>'
        '<div class="card">'
        '<form action="/scan"><button type="submit" style="background:#444">'
        'Rescan</button></form></div>'
        '</body></html>'
    )

# --- Areas config pages ---

def serve_areas(conn):
    """Stream areas page to socket — never builds full HTML in RAM."""
    alert_cfg = load_alert_cfg()
    try:
        with open(AREA_NAMES_FILE, 'r') as f:
            names = json.load(f)
    except:
        names = []
    gc.collect()

    conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n')
    conn.send(('<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Areas</title>' + STYLE + '</head><body>'
        '<h1>Alert Areas</h1>' + nav_bar() +
        '<div class="card"><p>Tap an area to select cities.</p>').encode('utf-8'))

    for area in names:
        if area in alert_cfg:
            sel = alert_cfg[area]
            badge = 'All' if sel == 'ALL' else str(len(sel))
            st = '<span style="color:#2ecc71">' + badge + '</span>'
        else:
            st = '<span style="color:#888">Off</span>'
        enc = ''
        for b in area.encode('utf-8'):
            enc += '%{:02X}'.format(b)
        conn.send(('<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 4px;border-bottom:1px solid #1f2b4d">'
            '<a href="/area?n=' + enc + '" style="flex:1">' + area + '</a>'
            '<span style="margin:0 10px">' + st + '</span></div>').encode('utf-8'))

    conn.send(b'</div><div class="card"><form method="POST" action="/clear_areas">'
        b'<button style="background:#555" type="submit">Clear All Areas</button>'
        b'</form></div></body></html>')
    conn.close()

def serve_area_detail(conn, area_name):
    """Stream one area's city checkboxes to socket."""
    alert_cfg = load_alert_cfg()
    sel = alert_cfg.get(area_name, None)
    del alert_cfg
    gc.collect()

    cities = []
    try:
        with open(AREAS_REF_FILE, 'r') as f:
            data = json.load(f)
        cities = data.get('areas', {}).get(area_name, [])
        del data
    except:
        pass
    gc.collect()

    is_all = (sel == 'ALL')

    conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n')
    conn.send(('<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>' + area_name + '</title>' + STYLE + '</head><body>'
        '<h1>' + area_name + '</h1>' + nav_bar() +
        '<form method="POST" action="/save_area">'
        '<input type="hidden" name="area" value="' + area_name + '">'
        '<div class="card">'
        '<label class="item"><input type="checkbox" name="select_all" value="1"'
        + (' checked' if is_all else '') + '> <b>All (' + str(len(cities)) + ')</b></label>'
        '<div class="scroll">').encode('utf-8'))

    for city in cities:
        chk = ' checked' if (is_all or (isinstance(sel, list) and city in sel)) else ''
        conn.send(('<label class="item"><input type="checkbox" name="city" value="'
            + city + '"' + chk + '> ' + city + '</label>').encode('utf-8'))

    conn.send(('</div></div><button type="submit">Save</button></form>'
        '<div class="card" style="margin-top:10px">'
        '<form method="POST" action="/area_off">'
        '<input type="hidden" name="area" value="' + area_name + '">'
        '<button style="background:#555" type="submit">Turn Off</button>'
        '</form></div>'
        '<p><a href="/areas">Back</a></p></body></html>').encode('utf-8'))
    conn.close()

# --- Test page ---

def page_test():
    status = 'ACTIVE' if _test_alert else 'Inactive'
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="refresh" content="5">'
        '<title>Test</title>' + STYLE + '</head><body>'
        '<h1>Test Alarm</h1>' + nav_bar() +
        _state_html() +
        '<div class="card"><p>Test injection: <b>' + status + '</b></p>'
        '<form method="POST" action="/test">'
        '<select name="type" style="width:100%;margin-bottom:8px">'
        '<option value="warn">\u05d4\u05ea\u05e8\u05e2\u05d4 (Warning)</option>'
        '<option value="shelter">\u05dc\u05de\u05e7\u05dc\u05d8 (Shelter)</option>'
        '<option value="clear">\u05dc\u05e6\u05d0\u05ea (Clear)</option>'
        '</select>'
        '<button type="submit" class="test-btn">Trigger Test</button></form>'
        '<form method="POST" action="/clear_test">'
        '<button type="submit" style="background:#555;margin-top:5px">Reset to Idle</button></form>'
        '</div>'
        '<div class="card"><form action="/reset_all" method="POST">'
        '<button style="background:#c0392b">Factory Reset (clear all config & reboot)</button>'
        '</form></div>'
        '</body></html>'
    )

def page_msg(msg):
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        + STYLE + '</head><body><h1>' + msg + '</h1>' + nav_bar() + '</body></html>'
    )

# --- HTTP ---

def send_response(conn, html):
    try:
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n')
        html_bytes = html.encode('utf-8')
        for i in range(0, len(html_bytes), 512):
            conn.send(html_bytes[i:i+512])
    except:
        pass
    try:
        conn.close()
    except:
        pass

def send_redirect(conn, location):
    try:
        conn.sendall(('HTTP/1.1 303 See Other\r\nLocation: ' + location + '\r\nConnection: close\r\n\r\n').encode())
    except:
        pass
    try:
        conn.close()
    except:
        pass

# --- Request handler ---

def handle_request(conn, mode, connected_ssid=''):
    global _test_alert
    try:
        data = conn.recv(4096)
        if not data:
            conn.close()
            return None
        request = data.decode('utf-8', 'ignore')
    except:
        try:
            conn.close()
        except:
            pass
        return None

    first_line = request.split('\r\n')[0]
    parts = first_line.split(' ')
    method = parts[0] if parts else 'GET'
    raw_path = parts[1] if len(parts) > 1 else '/'
    path = raw_path.split('?')[0] if '?' in raw_path else raw_path

    body = ''
    if '\r\n\r\n' in request:
        body = request.split('\r\n\r\n', 1)[1]

    action = None

    # === AP mode routes ===
    if mode == 'ap':
        if path == '/save_wifi' and method == 'POST':
            qs = parse_qs_multi(body)
            ssid = qs.get('ssid', [''])[0]
            pwd = qs.get('password', [''])[0]
            if ssid:
                nets = load_wifi()
                nets.append({'ssid': ssid, 'password': pwd})
                save_wifi(nets)
                send_response(conn, page_msg('WiFi Saved! Rebooting...'))
                action = 'reboot'
            else:
                send_response(conn, page_msg('Error: no SSID'))
        elif path == '/scan':
            send_response(conn, '<!DOCTYPE html><html><head><meta http-equiv="refresh" content="5;url=/"></head><body><h1>Rescanning...</h1></body></html>')
            action = 'rescan'
        else:
            send_response(conn, page_wifi_setup(_cached_networks))
        return action

    # === Station mode routes ===

    # -- WiFi management --
    if path == '/wifi':
        send_response(conn, page_wifi_manage())
    elif path == '/wifi_add' and method == 'POST':
        qs = parse_qs_multi(body)
        ssid = qs.get('ssid', [''])[0]
        pwd = qs.get('password', [''])[0]
        if ssid:
            nets = load_wifi()
            nets = [n for n in nets if n.get('ssid') != ssid]
            nets.append({'ssid': ssid, 'password': pwd})
            save_wifi(nets)
        send_redirect(conn, '/wifi')
    elif path == '/wifi_del' and method == 'POST':
        qs = parse_qs_multi(body)
        idx = int(qs.get('idx', ['0'])[0])
        nets = load_wifi()
        if 0 <= idx < len(nets):
            nets.pop(idx)
            save_wifi(nets)
        send_redirect(conn, '/wifi')
    elif path == '/wifi_del_all' and method == 'POST':
        save_wifi([])
        send_redirect(conn, '/wifi')

    # -- Areas config --
    elif path == '/areas':
        serve_areas(conn)
        return action
    elif path == '/area':
        area_name = ''
        if '?n=' in raw_path:
            area_name = url_decode(raw_path.split('?n=', 1)[1])
        if area_name:
            serve_area_detail(conn, area_name)
            return action
        else:
            send_redirect(conn, '/areas')
    elif path == '/save_area' and method == 'POST':
        qs = parse_qs_multi(body)
        area_name = qs.get('area', [''])[0]
        if area_name:
            alert_cfg = load_alert_cfg()
            if 'select_all' in qs:
                alert_cfg[area_name] = 'ALL'
            elif 'city' in qs:
                alert_cfg[area_name] = qs['city']
            else:
                alert_cfg.pop(area_name, None)
            save_alert_cfg(alert_cfg)
            refresh_monitored_cache()
        send_redirect(conn, '/areas')
    elif path == '/area_off' and method == 'POST':
        qs = parse_qs_multi(body)
        area_name = qs.get('area', [''])[0]
        if area_name:
            alert_cfg = load_alert_cfg()
            alert_cfg.pop(area_name, None)
            save_alert_cfg(alert_cfg)
            refresh_monitored_cache()
        send_redirect(conn, '/areas')
    elif path == '/clear_areas' and method == 'POST':
        save_alert_cfg({})
        refresh_monitored_cache()
        send_redirect(conn, '/areas')

    # -- Test --
    elif path == '/test_page':
        send_response(conn, page_test())
    elif path == '/test' and method == 'POST':
        qs = parse_qs_multi(body)
        test_type = qs.get('type', ['warn'])[0]
        if test_type == 'shelter':
            _test_alert = {'title': '\u05d9\u05e8\u05d9 \u05e8\u05e7\u05d8\u05d5\u05ea \u05d5\u05d8\u05d9\u05dc\u05d9\u05dd', 'cities': ['Test'], 'desc': 'Test'}
        elif test_type == 'clear':
            _test_alert = {'title': '\u05d4\u05d0\u05d9\u05e8\u05d5\u05e2 \u05d4\u05e1\u05ea\u05d9\u05d9\u05dd', 'cities': ['Test'], 'desc': 'Test'}
        else:
            _test_alert = {'title': '\u05d1\u05d3\u05e7\u05d5\u05ea \u05d4\u05e7\u05e8\u05d5\u05d1\u05d5\u05ea \u05e6\u05e4\u05d5\u05d9\u05d5\u05ea \u05dc\u05d4\u05ea\u05e7\u05d1\u05dc \u05d4\u05ea\u05e8\u05e2\u05d5\u05ea', 'cities': ['Test'], 'desc': 'Test'}
        send_redirect(conn, '/test_page')
    elif path == '/clear_test' and method == 'POST':
        _test_alert = None
        send_redirect(conn, '/test_page')

    # -- Reset --
    elif path == '/reset_all' and method == 'POST':
        clear_all_config()
        send_response(conn, page_msg('All config cleared. Rebooting...'))
        action = 'reboot'

    # -- Home --
    else:
        send_response(conn, page_home(connected_ssid))

    return action

# --- Alert checking (persistent SSL connection) ---

def _alert_connect():
    """Establish persistent SSL connection. Call BEFORE starting web server."""
    global _alert_conn
    _alert_close()
    gc.collect()
    import esp32
    heap = esp32.idf_heap_info(esp32.HEAP_DATA)
    print('IDF heap:', heap)
    print('Python free:', gc.mem_free())
    import ssl
    addr = socket.getaddrinfo(ALERT_HOST, 443)[0][-1]
    s = socket.socket()
    s.settimeout(10)
    s.connect(addr)
    _alert_conn = ssl.wrap_socket(s, server_hostname=ALERT_HOST)
    print('Alert TLS connected')

def _alert_close():
    """Close persistent SSL connection."""
    global _alert_conn
    if _alert_conn:
        try:
            _alert_conn.close()
        except:
            pass
        _alert_conn = None

def _alert_read_body():
    """Read one HTTP response from the persistent connection, return body."""
    c = _alert_conn
    # Read headers byte-by-byte until \r\n\r\n
    hdr = b''
    while b'\r\n\r\n' not in hdr:
        b = c.read(1)
        if not b:
            raise OSError('conn closed')
        hdr += b
        if len(hdr) > 1024:
            raise OSError('hdr overflow')

    # Parse content-length and connection header
    cl = 0
    must_close = False
    for line in hdr.decode().split('\r\n'):
        ll = line.lower()
        if ll.startswith('content-length:'):
            cl = int(line.split(':', 1)[1].strip())
        elif ll.startswith('connection:') and 'close' in ll:
            must_close = True
    del hdr

    # Read exactly cl bytes of body
    body = b''
    while len(body) < cl:
        chunk = c.read(min(512, cl - len(body)))
        if not chunk:
            break
        body += chunk

    if must_close:
        _alert_close()

    return body.decode('utf-8', 'ignore')

def check_alerts():
    global _test_alert
    if _test_alert:
        return _test_alert

    if not _monitored_cities:
        _alert_close()
        return None

    if not _alert_conn:
        return None

    try:
        _alert_conn.write(ALERT_REQUEST)
        raw = _alert_read_body()

        if raw and raw[0] == '\ufeff':
            raw = raw[1:]
        raw = raw.strip()

        if not raw or raw in ('""', '{}', '[]'):
            return None

        # Log ALL raw responses for debugging (captures release messages etc.)
        print('RAW ALERT RESPONSE:', raw)

        data = json.loads(raw)
        del raw

        # Log parsed structure
        print('PARSED ALERT:', data)

        if not data or 'data' not in data or not data['data']:
            return None

        alert_cities = data['data']
        if isinstance(alert_cities, str):
            alert_cities = [alert_cities]

        title = data.get('title', 'Alert')
        desc = data.get('desc', '')
        del data

        for ac in alert_cities:
            for mc in _monitored_cities:
                if mc in ac or ac in mc:
                    return {'title': title, 'cities': alert_cities, 'desc': desc}

        return None
    except Exception as e:
        print('Alert poll error:', e)
        _alert_close()
        gc.collect()
        return None

# --- Main loops ---

def run_ap_mode():
    global _cached_networks
    show(['Scanning WiFi...'])
    _cached_networks = scan_networks()
    show(['AP Mode', 'SSID:', AP_SSID, 'Pass:', AP_PASSWORD, '-> 192.168.4.1'])
    startup_beep()
    ip = start_ap()

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', 80))
    srv.listen(1)
    print('AP mode - IP:', ip)

    while True:
        try:
            conn, addr = srv.accept()
            action = handle_request(conn, 'ap')
            if action == 'reboot':
                time.sleep(2)
                reset()
            elif action == 'rescan':
                srv.close()
                show(['Rescanning...'])
                _cached_networks = scan_networks()
                show(['AP Mode', 'SSID:', AP_SSID, 'Pass:', AP_PASSWORD, '-> 192.168.4.1'])
                start_ap()
                srv = socket.socket()
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(('0.0.0.0', 80))
                srv.listen(1)
        except Exception as e:
            print('AP error:', e)

def run_station_mode(connected_ssid, ip):
    global _alert_state
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', 80))
    srv.listen(1)
    srv.setblocking(False)

    last_poll = 0
    reconnect_after = 0
    siren_count = 0
    siren_silenced = False
    state_changed_at = 0
    MAX_SIREN_PLAYS = 2
    CLEAR_TIMEOUT_MS = 60000

    _alert_state = STATE_IDLE

    print('Station mode - IP:', ip)

    while True:
        gc.collect()
        now = time.ticks_ms()

        # 1. Handle web requests (non-blocking)
        try:
            conn, addr = srv.accept()
            conn.settimeout(5)
            action = handle_request(conn, 'sta', connected_ssid)
            if action == 'reboot':
                time.sleep(2)
                reset()
        except OSError:
            pass

        # 2. Poll alerts
        if time.ticks_diff(now, last_poll) > POLL_INTERVAL_MS:
            last_poll = now
            result = check_alerts()
            if result:
                title = result.get('title', '')
                new_state = classify_alert(title)
                if new_state is not None and new_state != _alert_state:
                    old = STATE_LABELS.get(_alert_state, '?')
                    _alert_state = new_state
                    state_changed_at = now
                    siren_count = 0
                    siren_silenced = False
                    print('STATE:', old, '->', STATE_LABELS.get(_alert_state, '?'))

        # 2b. Auto-clear CLEAR state after 60 seconds
        if _alert_state == STATE_CLEAR:
            if time.ticks_diff(now, state_changed_at) > CLEAR_TIMEOUT_MS:
                _alert_state = STATE_IDLE
                siren_silenced = True
                print('CLEAR expired -> IDLE')

        # 2c. Reconnect TLS if dropped
        if not _alert_conn and _monitored_cities:
            if reconnect_after == 0:
                reconnect_after = time.ticks_add(now, 5000)
            elif time.ticks_diff(now, reconnect_after) > 0:
                reconnect_after = 0
                print('TLS reconnect: closing web server...')
                srv.close()
                gc.collect()
                try:
                    _alert_connect()
                except Exception as e:
                    print('TLS reconnect failed:', e)
                    _alert_close()
                srv = socket.socket()
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(('0.0.0.0', 80))
                srv.listen(1)
                srv.setblocking(False)

        # 3. Display
        if _alert_state != STATE_IDLE:
            show_state_bitmap(_alert_state)
        else:
            alert_cfg = load_alert_cfg()
            n = len(alert_cfg)
            status = str(n) + ' areas' if n else 'Not set'
            tls = 'TLS:OK' if _alert_conn else 'TLS:--'
            show(['Emergency Alert', 'IP: ' + ip, '', 'Monitoring:', status, tls])

        # 4. Sound: siren for WARNING/SHELTER, beep for CLEAR
        if _alert_state in (STATE_WARNING, STATE_SHELTER) and not siren_silenced:
            if siren_count < MAX_SIREN_PLAYS:
                pressed = siren()
                if pressed:
                    siren_silenced = True
                    buzzer_pwm.duty(0)
                    while not button.value():
                        time.sleep_ms(50)
                    time.sleep_ms(300)
                else:
                    siren_count += 1
                    if siren_count >= MAX_SIREN_PLAYS:
                        siren_silenced = True
            else:
                time.sleep_ms(100)
        elif _alert_state == STATE_CLEAR and not siren_silenced:
            buzz(200, 880)
            time.sleep_ms(100)
            buzz(200, 880)
            siren_silenced = True
        else:
            time.sleep_ms(100)

        # 5. Button: just stop buzzer (no state change)
        if not button.value() and not siren_silenced:
            if _alert_state in (STATE_WARNING, STATE_SHELTER):
                siren_silenced = True
                buzzer_pwm.duty(0)
                while not button.value():
                    time.sleep_ms(50)
                time.sleep_ms(300)

def main():
    show(['Emergency Alert', '', 'Hold btn 3s', 'to reset cfg'])
    time.sleep_ms(500)

    if not button.value():
        held_start = time.ticks_ms()
        while not button.value():
            elapsed = time.ticks_diff(time.ticks_ms(), held_start)
            progress = min(elapsed // 600, 5)
            show(['Resetting' + '.' * progress, '', 'Keep holding...'])
            if elapsed > 3000:
                clear_all_config()
                show(['Config reset!', '', 'Starting AP...'])
                buzz(200, 1000)
                time.sleep(1)
                break
            time.sleep_ms(100)

    networks = load_wifi()
    if not networks:
        run_ap_mode()
        return

    show(['Connecting...'])
    ssid, ip = connect_wifi_multi(networks)

    if not ip:
        show(['WiFi FAILED!', '', 'Starting AP...', '', 'Hold btn on boot', 'to reset'])
        buzz(500, 300)
        time.sleep(3)
        run_ap_mode()
        return

    show(['Connected!', 'IP: ' + ip, 'WiFi: ' + ssid[:16], '', 'Connecting to', 'alert server...'])
    startup_beep()
    refresh_monitored_cache()

    # Establish TLS BEFORE starting web server (max IDF heap available)
    if _monitored_cities:
        show(['TLS handshake...'])
        try:
            _alert_connect()
            show(['Connected!', 'IP: ' + ip, 'WiFi: ' + ssid[:16], '', 'TLS: OK', 'Starting...'])
        except Exception as e:
            print('Initial TLS failed:', e)
            _alert_close()
            show(['Connected!', 'IP: ' + ip, 'WiFi: ' + ssid[:16], '', 'TLS: FAILED', 'Will retry...'])
    time.sleep(2)

    run_station_mode(ssid, ip)

main()
