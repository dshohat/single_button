// ****************************************
// Emergency Alert System for ESP32 (Arduino/PlatformIO)
// Connects to Israeli Home Front Command (Pikud HaOref) Red Alert system
//
// Hardware: ESP32-WROOM, SSD1306 128x64 OLED (I2C), buzzer (PWM), button
// Pins: SDA=21, SCL=22, Button=4(INPUT_PULLUP), Buzzer=23
//
// Config files on LittleFS:
//   /wifi_config.json  - saved WiFi networks [{ssid, password}, ...]
//   /alert_areas.json  - monitored areas/cities {area: [city,...] or "ALL"}
//   /areas.json        - master list of all areas->cities (read-only)
//   /area_names.json   - list of area names (read-only)
//
// Flow:
// 1. No saved WiFi -> AP "ESP32-Alert" (pass: 12345678) -> 192.168.4.1
// 2. Tries each saved network until one connects
// 3. Establishes persistent TLS to oref.org.il
// 4. Polls alerts every 3s, web UI on device IP
// 5. Hold button 3s on boot to clear all config
// ****************************************

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <WebServer.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <vector>
#include <algorithm>

// ===== Pins =====
#define BTN_PIN    4
#define BUZZER_PIN 23
#define OLED_SDA   21
#define OLED_SCL   22

// ===== Display =====
#define SCREEN_W 128
#define SCREEN_H 64
Adafruit_SSD1306 oled(SCREEN_W, SCREEN_H, &Wire, -1);

// ===== Constants =====
static const char* AP_SSID    = "ESP32-Alert";
static const char* AP_PASS    = "12345678";
static const char* ALERT_HOST = "www.oref.org.il";
static const char* ALERT_PATH = "/WarningMessages/alert/alerts.json";
static const char* WIFI_FILE  = "/wifi_config.json";
static const char* AREAS_CFG  = "/alert_areas.json";
static const char* AREAS_REF  = "/areas.json";
static const char* NAMES_FILE = "/area_names.json";
static const unsigned long POLL_MS = 3000;

// ===== Globals =====
WebServer srv(80);
WiFiClientSecure tlsClient;

bool isAP = false;
bool alertActive = false;
bool alertSilenced = false;
bool testAlertOn = false;
bool tlsOK = false;
unsigned long lastPoll = 0;
bool displayToggle = true;
String mySSID;
String myIP;
std::vector<String> monitoredCities;
String scanResultsHTML;

// ===== Display helpers =====

void show6(const char* a, const char* b = "", const char* c = "",
           const char* d = "", const char* e = "", const char* f = "") {
    oled.clearDisplay();
    oled.setTextSize(1);
    oled.setTextColor(SSD1306_WHITE);
    const char* lines[] = {a, b, c, d, e, f};
    for (int i = 0; i < 6; i++) {
        oled.setCursor(0, i * 10);
        String s = lines[i];
        oled.print(s.substring(0, 21));
    }
    oled.display();
}

// ===== Buzzer =====

void initBuzzer() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcAttach(BUZZER_PIN, 5000, 8);
#else
    ledcSetup(0, 5000, 8);
    ledcAttachPin(BUZZER_PIN, 0);
#endif
}

void buzzOn(int freq) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWriteTone(BUZZER_PIN, freq);
#else
    ledcWriteTone(0, freq);
#endif
}

void buzzOff() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWriteTone(BUZZER_PIN, 0);
#else
    ledcWriteTone(0, 0);
#endif
}

void buzz(int ms, int freq) {
    buzzOn(freq);
    delay(ms);
    buzzOff();
}

void siren() {
    for (int f = 400; f < 900; f += 30) { buzzOn(f); delay(12); }
    for (int f = 900; f > 400; f -= 30) { buzzOn(f); delay(12); }
    buzzOff();
}

void startupBeep() {
    buzz(100, 660);
    delay(50);
    buzz(100, 880);
}

// ===== URL helpers =====

String urlEncode(const String& s) {
    String out;
    out.reserve(s.length() * 3);
    for (unsigned int i = 0; i < s.length(); i++) {
        char c = s.charAt(i);
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~')
            out += c;
        else {
            char buf[4];
            snprintf(buf, sizeof(buf), "%%%02X", (uint8_t)c);
            out += buf;
        }
    }
    return out;
}

// ===== Config I/O =====

struct WifiNet { String ssid; String password; };

std::vector<WifiNet> loadWifi() {
    std::vector<WifiNet> nets;
    File f = LittleFS.open(WIFI_FILE, "r");
    if (!f) return nets;
    JsonDocument doc;
    if (deserializeJson(doc, f)) { f.close(); return nets; }
    f.close();
    for (JsonObject o : doc.as<JsonArray>()) {
        WifiNet n;
        n.ssid = o["ssid"].as<String>();
        n.password = o["password"].as<String>();
        nets.push_back(n);
    }
    return nets;
}

void saveWifi(const std::vector<WifiNet>& nets) {
    JsonDocument doc;
    JsonArray arr = doc.to<JsonArray>();
    for (const auto& n : nets) {
        JsonObject o = arr.add<JsonObject>();
        o["ssid"] = n.ssid;
        o["password"] = n.password;
    }
    File f = LittleFS.open(WIFI_FILE, "w");
    if (f) { serializeJson(doc, f); f.close(); }
}

JsonDocument loadAlertCfg() {
    JsonDocument doc;
    File f = LittleFS.open(AREAS_CFG, "r");
    if (!f) return doc;
    deserializeJson(doc, f);
    f.close();
    return doc;
}

void saveAlertCfg(const JsonDocument& doc) {
    File f = LittleFS.open(AREAS_CFG, "w");
    if (f) { serializeJson(doc, f); f.close(); }
}

std::vector<String> loadAreaNames() {
    std::vector<String> names;
    File f = LittleFS.open(NAMES_FILE, "r");
    if (!f) return names;
    JsonDocument doc;
    if (deserializeJson(doc, f)) { f.close(); return names; }
    f.close();
    for (JsonVariant v : doc.as<JsonArray>()) {
        names.push_back(v.as<String>());
    }
    return names;
}

std::vector<String> loadAreaCities(const String& areaName) {
    std::vector<String> cities;
    File f = LittleFS.open(AREAS_REF, "r");
    if (!f) return cities;
    JsonDocument doc;
    if (deserializeJson(doc, f)) { f.close(); return cities; }
    f.close();
    JsonArray arr = doc["areas"][areaName].as<JsonArray>();
    if (arr) {
        for (JsonVariant v : arr) {
            cities.push_back(v.as<String>());
        }
    }
    return cities;
}

void clearAllConfig() {
    LittleFS.remove(WIFI_FILE);
    LittleFS.remove(AREAS_CFG);
}

// ===== Monitored Cities Cache =====

void refreshMonitoredCities() {
    monitoredCities.clear();
    JsonDocument cfg = loadAlertCfg();
    if (cfg.isNull() || cfg.as<JsonObject>().size() == 0) return;

    File f = LittleFS.open(AREAS_REF, "r");
    if (!f) return;
    JsonDocument ref;
    if (deserializeJson(ref, f)) { f.close(); return; }
    f.close();

    for (JsonPair kv : cfg.as<JsonObject>()) {
        const char* area = kv.key().c_str();
        JsonArray refCities = ref["areas"][area].as<JsonArray>();
        if (!refCities) continue;

        if (kv.value().is<const char*>() && String(kv.value().as<const char*>()) == "ALL") {
            for (JsonVariant c : refCities)
                monitoredCities.push_back(c.as<String>());
        } else if (kv.value().is<JsonArray>()) {
            for (JsonVariant c : kv.value().as<JsonArray>())
                monitoredCities.push_back(c.as<String>());
        }
    }
    Serial.printf("Monitoring %d cities\n", monitoredCities.size());
}

// ===== WiFi =====

String scanWifiNetworks() {
    WiFi.mode(WIFI_STA);
    int n = WiFi.scanNetworks();
    String opts;
    for (int i = 0; i < n; i++) {
        String ssid = WiFi.SSID(i);
        int rssi = WiFi.RSSI(i);
        opts += "<option value=\"" + ssid + "\">" + ssid + " (" + String(rssi) + "dBm)</option>";
    }
    WiFi.scanDelete();
    return opts;
}

bool connectWifi(const std::vector<WifiNet>& nets, int timeout = 15) {
    WiFi.mode(WIFI_STA);
    for (const auto& net : nets) {
        if (net.ssid.isEmpty()) continue;
        show6("Trying:", net.ssid.c_str(), "...");
        WiFi.begin(net.ssid.c_str(), net.password.c_str());
        unsigned long start = millis();
        while (WiFi.status() != WL_CONNECTED) {
            if (millis() - start > (unsigned long)timeout * 1000) break;
            delay(500);
        }
        if (WiFi.status() == WL_CONNECTED) {
            mySSID = net.ssid;
            myIP = WiFi.localIP().toString();
            return true;
        }
        WiFi.disconnect();
    }
    return false;
}

void startAP() {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASS);
    myIP = WiFi.softAPIP().toString();
}

// ===== CSS & HTML helpers =====

static const char CSS[] = R"rawliteral(<style>
body{font-family:sans-serif;margin:20px;direction:rtl;background:#1a1a2e;color:#eee}
h1,h2{color:#e94560}
input,select,textarea,button{font-size:16px;padding:8px;margin:5px 0;box-sizing:border-box;direction:rtl}
button{background:#e94560;color:#fff;border:none;border-radius:4px;cursor:pointer;width:100%}
button:hover{background:#c73e54}
.card{background:#16213e;padding:15px;border-radius:8px;margin:10px 0}
a{color:#e94560}
nav{margin:10px 0}nav a{margin-left:15px;font-size:15px}
label.item{display:block;padding:6px 4px;font-size:15px;cursor:pointer}
label.item:hover{background:#1f2b4d;border-radius:4px}
input[type=checkbox]{width:18px;height:18px;margin-left:8px}
.del-btn{background:#c0392b;width:auto;padding:4px 12px;font-size:13px;margin-right:8px}
.test-btn{background:#ff9800}.test-btn:hover{background:#e68a00}
.scroll{max-height:300px;overflow-y:auto;background:#0f1a30;padding:8px;border-radius:4px}
</style>)rawliteral";

static const char NAV[] = R"rawliteral(<nav><a href="/">Home</a><a href="/wifi">WiFi</a><a href="/areas">Areas</a><a href="/test_page">Test</a></nav><hr>)rawliteral";

String htmlHead(const char* title) {
    return String("<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>") + title + "</title>" + CSS + "</head><body>";
}

void sendRedirect(const String& url) {
    srv.sendHeader("Location", url);
    srv.send(303, "text/plain", "");
}

// ===== Web Handlers: Home =====

void handleHome() {
    JsonDocument cfg = loadAlertCfg();
    int n = cfg.as<JsonObject>().size();
    String html = htmlHead("Emergency Alerts")
        + "<h1>Emergency Alerts</h1>" + NAV
        + "<div class=\"card\"><h2>Status</h2>"
        + "<p>Connected to: <b>" + mySSID + "</b></p>"
        + "<p>Monitoring: <b>" + String(n) + " area(s)</b></p>"
        + "<p>TLS: <b>" + (tlsOK ? "Connected" : "Disconnected") + "</b></p></div>"
        + "</body></html>";
    srv.send(200, "text/html; charset=utf-8", html);
}

// ===== Web Handlers: WiFi Management =====

void handleWifi() {
    auto nets = loadWifi();
    String rows;
    for (int i = 0; i < (int)nets.size(); i++) {
        rows += "<div style=\"display:flex;align-items:center;justify-content:space-between;padding:4px 0\">"
            "<span>" + nets[i].ssid + "</span>"
            "<form method=\"POST\" action=\"/wifi_del\" style=\"margin:0\">"
            "<input type=\"hidden\" name=\"idx\" value=\"" + String(i) + "\">"
            "<button class=\"del-btn\" type=\"submit\">Delete</button>"
            "</form></div>";
    }
    if (rows.isEmpty()) rows = "<p>No saved networks.</p>";
    String html = htmlHead("WiFi")
        + "<h1>WiFi Networks</h1>" + NAV
        + "<div class=\"card\"><h2>Saved Networks</h2>" + rows
        + "<form method=\"POST\" action=\"/wifi_del_all\" style=\"margin-top:10px\">"
        + "<button style=\"background:#555\" type=\"submit\">Delete All Networks</button>"
        + "</form></div>"
        + "<div class=\"card\"><h2>Add Network</h2>"
        + "<form method=\"POST\" action=\"/wifi_add\">"
        + "<label>SSID:</label><input type=\"text\" name=\"ssid\" style=\"width:100%\"><br>"
        + "<label>Password:</label><input type=\"password\" name=\"password\" style=\"width:100%\"><br><br>"
        + "<button type=\"submit\">Add Network</button>"
        + "</form></div></body></html>";
    srv.send(200, "text/html; charset=utf-8", html);
}

void handleWifiAdd() {
    String ssid = srv.arg("ssid");
    String pwd = srv.arg("password");
    if (ssid.length() > 0) {
        auto nets = loadWifi();
        nets.erase(std::remove_if(nets.begin(), nets.end(),
            [&](const WifiNet& n) { return n.ssid == ssid; }), nets.end());
        WifiNet n;
        n.ssid = ssid;
        n.password = pwd;
        nets.push_back(n);
        saveWifi(nets);
    }
    sendRedirect("/wifi");
}

void handleWifiDel() {
    int idx = srv.arg("idx").toInt();
    auto nets = loadWifi();
    if (idx >= 0 && idx < (int)nets.size()) {
        nets.erase(nets.begin() + idx);
        saveWifi(nets);
    }
    sendRedirect("/wifi");
}

void handleWifiDelAll() {
    std::vector<WifiNet> empty;
    saveWifi(empty);
    sendRedirect("/wifi");
}

// ===== Web Handlers: Areas =====

void handleAreas() {
    auto names = loadAreaNames();
    JsonDocument cfg = loadAlertCfg();

    srv.setContentLength(CONTENT_LENGTH_UNKNOWN);
    srv.send(200, "text/html; charset=utf-8", "");
    srv.sendContent(htmlHead("Areas"));
    srv.sendContent(String("<h1>Alert Areas</h1>") + NAV);
    srv.sendContent("<div class=\"card\"><p>Tap an area to select cities.</p>");

    for (const auto& area : names) {
        String badge;
        if (cfg[area].is<const char*>() && String(cfg[area].as<const char*>()) == "ALL") {
            badge = "<span style=\"color:#2ecc71\">All</span>";
        } else if (cfg[area].is<JsonArray>()) {
            badge = "<span style=\"color:#2ecc71\">" + String(cfg[area].as<JsonArray>().size()) + "</span>";
        } else {
            badge = "<span style=\"color:#888\">Off</span>";
        }
        String enc = urlEncode(area);
        srv.sendContent("<div style=\"display:flex;align-items:center;justify-content:space-between;"
            "padding:8px 4px;border-bottom:1px solid #1f2b4d\">"
            "<a href=\"/area?n=" + enc + "\" style=\"flex:1\">" + area + "</a>"
            "<span style=\"margin:0 10px\">" + badge + "</span></div>");
    }

    srv.sendContent("</div><div class=\"card\"><form method=\"POST\" action=\"/clear_areas\">"
        "<button style=\"background:#555\" type=\"submit\">Clear All Areas</button>"
        "</form></div></body></html>");
    srv.sendContent("");
}

void handleAreaDetail() {
    String areaName = srv.arg("n");
    if (areaName.isEmpty()) { sendRedirect("/areas"); return; }

    auto cities = loadAreaCities(areaName);
    JsonDocument cfg = loadAlertCfg();

    bool isAll = false;
    std::vector<String> selected;
    if (cfg[areaName].is<const char*>() && String(cfg[areaName].as<const char*>()) == "ALL") {
        isAll = true;
    } else if (cfg[areaName].is<JsonArray>()) {
        for (JsonVariant v : cfg[areaName].as<JsonArray>())
            selected.push_back(v.as<String>());
    }

    srv.setContentLength(CONTENT_LENGTH_UNKNOWN);
    srv.send(200, "text/html; charset=utf-8", "");
    srv.sendContent(htmlHead(areaName.c_str()));
    srv.sendContent("<h1>" + areaName + "</h1>");
    srv.sendContent(String(NAV));
    srv.sendContent("<form method=\"POST\" action=\"/save_area\">"
        "<input type=\"hidden\" name=\"area\" value=\"" + areaName + "\">"
        "<div class=\"card\">"
        "<label class=\"item\"><input type=\"checkbox\" name=\"select_all\" value=\"1\"");
    if (isAll) srv.sendContent(" checked");
    srv.sendContent("> <b>All (" + String(cities.size()) + ")</b></label>"
        "<div class=\"scroll\">");

    for (const auto& city : cities) {
        bool checked = isAll;
        if (!checked) {
            for (const auto& s : selected) {
                if (s == city) { checked = true; break; }
            }
        }
        srv.sendContent("<label class=\"item\"><input type=\"checkbox\" name=\"city\" value=\""
            + city + "\"" + (checked ? String(" checked") : String("")) + "> " + city + "</label>");
    }

    srv.sendContent("</div></div><button type=\"submit\">Save</button></form>"
        "<div class=\"card\" style=\"margin-top:10px\">"
        "<form method=\"POST\" action=\"/area_off\">"
        "<input type=\"hidden\" name=\"area\" value=\"" + areaName + "\">"
        "<button style=\"background:#555\" type=\"submit\">Turn Off</button>"
        "</form></div>"
        "<p><a href=\"/areas\">Back</a></p></body></html>");
    srv.sendContent("");
}

void handleSaveArea() {
    String areaName = srv.arg("area");
    if (areaName.isEmpty()) { sendRedirect("/areas"); return; }

    JsonDocument cfg = loadAlertCfg();
    bool selectAll = false;
    std::vector<String> cities;
    for (int i = 0; i < srv.args(); i++) {
        if (srv.argName(i) == "select_all") selectAll = true;
        if (srv.argName(i) == "city") cities.push_back(srv.arg(i));
    }

    if (selectAll) {
        cfg[areaName] = "ALL";
    } else if (!cities.empty()) {
        JsonArray arr = cfg[areaName].to<JsonArray>();
        for (const auto& c : cities) arr.add(c);
    } else {
        cfg.remove(areaName);
    }

    saveAlertCfg(cfg);
    refreshMonitoredCities();
    sendRedirect("/areas");
}

void handleAreaOff() {
    String areaName = srv.arg("area");
    if (!areaName.isEmpty()) {
        JsonDocument cfg = loadAlertCfg();
        cfg.remove(areaName);
        saveAlertCfg(cfg);
        refreshMonitoredCities();
    }
    sendRedirect("/areas");
}

void handleClearAreas() {
    JsonDocument doc;
    doc.to<JsonObject>();
    saveAlertCfg(doc);
    refreshMonitoredCities();
    sendRedirect("/areas");
}

// ===== Web Handlers: Test =====

void handleTestPage() {
    String status = testAlertOn ? "ACTIVE" : "Inactive";
    String html = htmlHead("Test")
        + "<h1>Test Alarm</h1>" + NAV
        + "<div class=\"card\"><p>Test alarm: <b>" + status + "</b></p>"
        + "<form method=\"POST\" action=\"/test\">"
        + "<button type=\"submit\" class=\"test-btn\">Trigger Test Alarm</button></form>"
        + "<form method=\"POST\" action=\"/clear_test\">"
        + "<button type=\"submit\" style=\"background:#555;margin-top:5px\">Stop Test Alarm</button></form>"
        + "</div>"
        + "<div class=\"card\"><form action=\"/reset_all\" method=\"POST\">"
        + "<button style=\"background:#c0392b\">Factory Reset</button>"
        + "</form></div></body></html>";
    srv.send(200, "text/html; charset=utf-8", html);
}

void handleTest() {
    testAlertOn = true;
    sendRedirect("/test_page");
}

void handleClearTest() {
    testAlertOn = false;
    alertActive = false;
    alertSilenced = false;
    sendRedirect("/test_page");
}

void handleResetAll() {
    clearAllConfig();
    String html = htmlHead("Reset") + "<h1>Config cleared. Rebooting...</h1></body></html>";
    srv.send(200, "text/html; charset=utf-8", html);
    delay(2000);
    ESP.restart();
}

// ===== Web Handlers: AP Mode =====

void handleAPSetup() {
    String html = htmlHead("WiFi Setup")
        + "<h1>ESP32 Alert - WiFi Setup</h1>"
        + "<div class=\"card\">"
        + "<form method=\"POST\" action=\"/save_wifi\">"
        + "<label>Select Network:</label>"
        + "<select name=\"ssid\">" + scanResultsHTML + "</select><br><br>"
        + "<label>Password:</label>"
        + "<input type=\"password\" name=\"password\"><br><br>"
        + "<button type=\"submit\">Connect & Reboot</button>"
        + "</form></div>"
        + "<div class=\"card\">"
        + "<form action=\"/scan\"><button type=\"submit\" style=\"background:#444\">"
        + "Rescan</button></form></div></body></html>";
    srv.send(200, "text/html; charset=utf-8", html);
}

void handleAPSave() {
    String ssid = srv.arg("ssid");
    String pwd = srv.arg("password");
    if (ssid.length() > 0) {
        auto nets = loadWifi();
        WifiNet n;
        n.ssid = ssid;
        n.password = pwd;
        nets.push_back(n);
        saveWifi(nets);
        String html = htmlHead("Saved") + "<h1>WiFi Saved! Rebooting...</h1></body></html>";
        srv.send(200, "text/html; charset=utf-8", html);
        delay(2000);
        ESP.restart();
    } else {
        String html = htmlHead("Error") + "<h1>Error: no SSID</h1></body></html>";
        srv.send(200, "text/html; charset=utf-8", html);
    }
}

void handleAPScan() {
    srv.send(200, "text/html; charset=utf-8",
        "<!DOCTYPE html><html><head><meta http-equiv=\"refresh\" content=\"5;url=/\"></head>"
        "<body><h1>Rescanning...</h1></body></html>");
    delay(500);
    scanResultsHTML = scanWifiNetworks();
    startAP();
}

// ===== Alert Polling (persistent TLS) =====

void connectTLS() {
    if (tlsClient.connected()) tlsClient.stop();
    tlsOK = false;
    tlsClient.setInsecure();
    tlsClient.setTimeout(10);

    Serial.printf("TLS: Connecting... Heap: %d, MaxBlock: %d\n",
        ESP.getFreeHeap(), ESP.getMaxAllocHeap());

    if (tlsClient.connect(ALERT_HOST, 443)) {
        tlsOK = true;
        Serial.println("TLS: Connected!");
    } else {
        Serial.println("TLS: Connection failed");
    }
}

bool checkAlerts() {
    if (testAlertOn) return true;
    if (monitoredCities.empty()) return false;

    // Reconnect if needed
    if (!tlsClient.connected()) {
        tlsOK = false;
        connectTLS();
        if (!tlsOK) return false;
    }

    // Send HTTP request over persistent connection
    tlsClient.print("GET ");
    tlsClient.print(ALERT_PATH);
    tlsClient.println(" HTTP/1.1");
    tlsClient.print("Host: ");
    tlsClient.println(ALERT_HOST);
    tlsClient.println("User-Agent: Mozilla/5.0");
    tlsClient.println("Referer: https://www.oref.org.il/");
    tlsClient.println("X-Requested-With: XMLHttpRequest");
    tlsClient.println("Connection: keep-alive");
    tlsClient.println();

    // Wait for response
    unsigned long start = millis();
    while (!tlsClient.available()) {
        if (millis() - start > 10000) {
            Serial.println("Alert: response timeout");
            tlsClient.stop();
            tlsOK = false;
            return false;
        }
        delay(10);
    }

    // Parse headers
    int contentLength = 0;
    bool mustClose = false;
    while (tlsClient.connected()) {
        String line = tlsClient.readStringUntil('\n');
        line.trim();
        if (line.isEmpty()) break;
        String ll = line;
        ll.toLowerCase();
        if (ll.startsWith("content-length:")) {
            contentLength = line.substring(line.indexOf(':') + 1).toInt();
        }
        if (ll.startsWith("connection:") && ll.indexOf("close") >= 0) {
            mustClose = true;
        }
    }

    // Read body
    String body;
    if (contentLength > 0) {
        body.reserve(contentLength);
        int bytesRead = 0;
        unsigned long bodyStart = millis();
        while (bytesRead < contentLength && tlsClient.connected()) {
            if (tlsClient.available()) {
                char c = tlsClient.read();
                body += c;
                bytesRead++;
            } else if (millis() - bodyStart > 5000) {
                break;
            } else {
                delay(1);
            }
        }
    }

    if (mustClose) {
        tlsClient.stop();
        tlsOK = false;
    }

    // Parse response
    body.trim();
    if (body.isEmpty() || body == "\"\"" || body == "{}" || body == "[]") return false;

    // Remove BOM
    if (body.length() > 2 && (uint8_t)body.charAt(0) == 0xEF
        && (uint8_t)body.charAt(1) == 0xBB && (uint8_t)body.charAt(2) == 0xBF) {
        body = body.substring(3);
        body.trim();
    }
    if (body.isEmpty()) return false;

    JsonDocument doc;
    if (deserializeJson(doc, body)) return false;
    if (!doc["data"].is<JsonArray>() || doc["data"].as<JsonArray>().size() == 0) return false;

    for (JsonVariant ac : doc["data"].as<JsonArray>()) {
        String alertCity = ac.as<String>();
        for (const auto& mc : monitoredCities) {
            if (alertCity.indexOf(mc) >= 0 || mc.indexOf(alertCity) >= 0) {
                Serial.println("ALERT MATCH: " + alertCity);
                return true;
            }
        }
    }
    return false;
}

// ===== Setup =====

void enterAPMode() {
    isAP = true;
    show6("Scanning WiFi...");
    scanResultsHTML = scanWifiNetworks();
    startAP();
    show6("AP Mode", ("SSID: " + String(AP_SSID)).c_str(),
          ("Pass: " + String(AP_PASS)).c_str(),
          ("-> " + myIP).c_str());
    startupBeep();

    srv.on("/", handleAPSetup);
    srv.on("/save_wifi", HTTP_POST, handleAPSave);
    srv.on("/scan", handleAPScan);
    srv.begin();
    Serial.println("AP mode - IP: " + myIP);
}

void setup() {
    Serial.begin(115200);
    Serial.println("\n\nEmergency Alert System (Arduino)");

    // Hardware
    Wire.begin(OLED_SDA, OLED_SCL);
    if (!oled.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
        Serial.println("OLED init failed!");
    }
    oled.clearDisplay();
    oled.display();
    initBuzzer();
    buzzOff();
    pinMode(BTN_PIN, INPUT_PULLUP);

    // Filesystem
    if (!LittleFS.begin(true)) {
        Serial.println("LittleFS mount failed!");
        show6("FS ERROR!", "", "Reflash device");
        while (1) delay(1000);
    }

    show6("Emergency Alert", "", "Hold btn 3s", "to reset cfg");
    delay(500);

    // Factory reset check
    if (digitalRead(BTN_PIN) == LOW) {
        unsigned long held = millis();
        while (digitalRead(BTN_PIN) == LOW) {
            unsigned long elapsed = millis() - held;
            int dots = min((int)(elapsed / 600), 5);
            String prog = "Resetting";
            for (int i = 0; i < dots; i++) prog += ".";
            show6(prog.c_str(), "", "Keep holding...");
            if (elapsed > 3000) {
                clearAllConfig();
                show6("Config reset!", "", "Starting AP...");
                buzz(200, 1000);
                delay(1000);
                break;
            }
            delay(100);
        }
    }

    // Load WiFi config
    auto nets = loadWifi();
    if (nets.empty()) {
        enterAPMode();
        return;
    }

    // Try connecting
    show6("Connecting...");
    if (!connectWifi(nets)) {
        show6("WiFi FAILED!", "", "Starting AP...", "", "Hold btn on boot", "to reset");
        buzz(500, 300);
        delay(3000);
        enterAPMode();
        return;
    }

    // Connected - station mode
    show6("Connected!", ("IP: " + myIP).c_str(),
          ("WiFi: " + mySSID).c_str(), "", "Starting...");
    startupBeep();
    refreshMonitoredCities();

    // Establish TLS before web server
    if (!monitoredCities.empty()) {
        show6("TLS handshake...");
        connectTLS();
        if (tlsOK) {
            show6("Connected!", ("IP: " + myIP).c_str(),
                  ("WiFi: " + mySSID).c_str(), "", "TLS: OK");
        } else {
            show6("Connected!", ("IP: " + myIP).c_str(),
                  ("WiFi: " + mySSID).c_str(), "", "TLS: FAILED", "Will retry...");
        }
    }
    delay(2000);

    // Setup station mode routes
    srv.on("/", handleHome);
    srv.on("/wifi", handleWifi);
    srv.on("/wifi_add", HTTP_POST, handleWifiAdd);
    srv.on("/wifi_del", HTTP_POST, handleWifiDel);
    srv.on("/wifi_del_all", HTTP_POST, handleWifiDelAll);
    srv.on("/areas", handleAreas);
    srv.on("/area", handleAreaDetail);
    srv.on("/save_area", HTTP_POST, handleSaveArea);
    srv.on("/area_off", HTTP_POST, handleAreaOff);
    srv.on("/clear_areas", HTTP_POST, handleClearAreas);
    srv.on("/test_page", handleTestPage);
    srv.on("/test", HTTP_POST, handleTest);
    srv.on("/clear_test", HTTP_POST, handleClearTest);
    srv.on("/reset_all", HTTP_POST, handleResetAll);
    srv.begin();

    Serial.println("Station mode - IP: " + myIP);
}

// ===== Loop =====

void loop() {
    srv.handleClient();

    if (isAP) {
        delay(10);
        return;
    }

    // Poll alerts
    unsigned long now = millis();
    if (now - lastPoll > POLL_MS) {
        lastPoll = now;
        bool hasAlert = checkAlerts();
        if (hasAlert && !alertActive) {
            alertActive = true;
            alertSilenced = false;
            Serial.println("ALERT ACTIVE!");
        } else if (!hasAlert && alertActive && !testAlertOn) {
            alertActive = false;
            alertSilenced = false;
            Serial.println("Alert cleared");
            buzz(100, 440);
        }
    }

    // Display
    if (alertActive) {
        displayToggle = !displayToggle;
        if (displayToggle) {
            show6("!! ALERT !!", "", "Red Alert!", "", "Press btn", "to silence");
        } else {
            show6("!! ALERT !!", "", "ACTIVE", "", "Press btn", "to silence");
        }
    } else {
        int n = monitoredCities.size();
        String status = n > 0 ? String(n) + " cities" : "Not set";
        String tls = tlsOK ? "TLS:OK" : "TLS:--";
        show6("Emergency Alert", ("IP: " + myIP).c_str(), "",
              "Monitoring:", status.c_str(), tls.c_str());
    }

    // Siren
    if (alertActive && !alertSilenced) {
        siren();
    } else {
        delay(100);
    }

    // Button to silence
    if (digitalRead(BTN_PIN) == LOW) {
        if (alertActive && !alertSilenced) {
            alertSilenced = true;
            buzzOff();
            show6("Alarm silenced", "", "Alert still", "active");
            buzz(50, 440);
            delay(500);
        }
    }
}
