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
#include <U8g2lib.h>
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <time.h>
#include <vector>
#include <algorithm>
#include "hebrew_bitmaps.h"

// ===== Pins =====
#define BTN_PIN    4
#define BUZZER_PIN 23
#define OLED_SDA   21
#define OLED_SCL   22

// ===== Display (U8g2 with Hebrew font) =====
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);

// ===== Constants =====
static const char* AP_SSID    = "ESP32-Alert";
static const char* AP_PASS    = "12345678";
static const char* ALERT_HOST = "www.oref.org.il";
static const char* ALERT_PATH = "/WarningMessages/alert/alerts.json";
static const char* WIFI_FILE  = "/wifi_config.json";
static const char* AREAS_CFG  = "/alert_areas.json";
static const char* AREAS_REF  = "/areas.json";
static const char* NAMES_FILE = "/area_names.json";
static const char* SETTINGS_FILE = "/settings.json";
static const unsigned long POLL_MS = 3000;

// ===== Globals =====
WebServer srv(80);
WiFiClientSecure tlsClient;

bool isAP = false;
bool testAlertOn = false;
String testInjectedBody;
bool   testHasInjection = false;
bool tlsOK = false;
unsigned long lastPoll = 0;
bool displayToggle = true;
String mySSID;
String myIP;
std::vector<String> monitoredCities;
String alertMatchCity;
String alertTitle;
String alertDesc;
int    alertCat = 0;
String scanResultsHTML;
unsigned long lastDisplaySwitch = 0;
int cityScrollOffset = 0;
bool showCityList = false;
unsigned long lastFlashToggle = 0;

// ===== Alert State Machine =====
enum AlertState { STATE_IDLE = 0, STATE_WARNING = 1, STATE_SHELTER = 2, STATE_CLEAR = 3 };
AlertState alertState = STATE_IDLE;
unsigned long stateChangedAt = 0;
int sirenCount = 0;
bool sirenSilenced = false;
static const unsigned long CLEAR_TIMEOUT_MS = 60000;

// ===== User Settings (persisted to LittleFS) =====
int cfgAlarmType = 0;      // 0 = La Mama, 1 = Mario
int cfgAlarmRepeats = 1;   // 1-99
bool cfgDefaultNight = false; // default night mode on power-on

const char* stateLabel(AlertState s) {
    switch(s) {
        case STATE_WARNING: return "Warning";
        case STATE_SHELTER: return "Shelter!";
        case STATE_CLEAR:   return "Clear";
        default:            return "Idle";
    }
}

// Classify alert title -> state (based on real Pikud HaOref messages)
AlertState classifyAlert(const String& title) {
    // cat=1: ירי רקטות / חדירת כלי טיס / רעידת אדמה / חומרים מסוכנים / חדירת מחבלים
    if (title.indexOf("\xD7\x99\xD7\xA8\xD7\x99 \xD7\xA8\xD7\xA7\xD7\x98\xD7\x95\xD7\xAA") >= 0) return STATE_SHELTER;
    if (title.indexOf("\xD7\x97\xD7\x93\xD7\x99\xD7\xA8\xD7\xAA") >= 0) return STATE_SHELTER;
    if (title.indexOf("\xD7\xA8\xD7\xA2\xD7\x99\xD7\x93\xD7\xAA") >= 0) return STATE_SHELTER;
    if (title.indexOf("\xD7\x97\xD7\x95\xD7\x9E\xD7\xA8\xD7\x99\xD7\x9D") >= 0) return STATE_SHELTER;
    // cat=10 warning: בדקות הקרובות / התרעה מוקדמת
    if (title.indexOf("\xD7\x91\xD7\x93\xD7\xA7\xD7\x95\xD7\xAA \xD7\x94\xD7\xA7\xD7\xA8\xD7\x95\xD7\x91\xD7\x95\xD7\xAA") >= 0) return STATE_WARNING;
    if (title.indexOf("\xD7\x94\xD7\xAA\xD7\xA8\xD7\xA2\xD7\x94 \xD7\x9E\xD7\x95\xD7\xA7\xD7\x93\xD7\x9E\xD7\xAA") >= 0) return STATE_WARNING;
    // cat=10 clear: האירוע הסתיים
    if (title.indexOf("\xD7\x94\xD7\x90\xD7\x99\xD7\xA8\xD7\x95\xD7\xA2 \xD7\x94\xD7\xA1\xD7\xAA\xD7\x99\xD7\x99\xD7\x9D") >= 0) return STATE_CLEAR;
    // Fallback: any cat 1-7,13 -> SHELTER, cat 10 -> WARNING
    return STATE_IDLE;  // unknown, caller handles cat-based fallback
}

// ===== Time helpers =====
void initNTP() {
    configTzTime("IST-2IDT,M3.4.5/02,M10.5.0/02", "pool.ntp.org", "time.nist.gov");
    Serial.print("Syncing NTP...");
    struct tm t;
    for (int i = 0; i < 20 && !getLocalTime(&t, 500); i++) Serial.print(".");
    if (getLocalTime(&t, 0)) {
        char buf[20];
        strftime(buf, sizeof(buf), "%d/%m/%y %H:%M:%S", &t);
        Serial.printf(" OK: %s\n", buf);
    } else {
        Serial.println(" failed (will retry in background)");
    }
}

String nowStr() {
    struct tm t;
    if (!getLocalTime(&t, 0)) return "--/--/-- --:--";
    char buf[18];
    strftime(buf, sizeof(buf), "%d/%m/%y %H:%M", &t);
    return String(buf);
}

// ===== Alert Log (file-based on LittleFS) =====
static const char* LOG_FILE = "/alert_log.txt";
bool nightMode = false;

void addAlertLog(int cat, const String& title, const String& desc, const String& city) {
    String ts = nowStr();
    Serial.printf("[%s] addAlertLog: cat=%d city=%s\n", ts.c_str(), cat, city.c_str());
    File f = LittleFS.open(LOG_FILE, "a");
    if (!f) { Serial.println("Failed to open log file"); return; }
    JsonDocument doc;
    doc["cat"] = cat;
    doc["title"] = title;
    doc["desc"] = desc;
    doc["city"] = city;
    doc["time"] = ts;
    serializeJson(doc, f);
    f.println();
    f.close();
}

// ===== Display helpers =====

void show6(const char* a, const char* b = "", const char* c = "",
           const char* d = "", const char* e = "", const char* f = "") {
    oled.clearBuffer();
    oled.setFont(u8g2_font_6x10_tf);
    const char* lines[] = {a, b, c, d, e, f};
    for (int i = 0; i < 6; i++) {
        String s = lines[i];
        oled.drawStr(0, (i + 1) * 10, s.substring(0, 21).c_str());
    }
    oled.sendBuffer();
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

// "E LA mama" tune - plays once, returns true if button pressed
bool laMamaSiren() {
    struct Note { int freq; int dur; };
    Note phrase[] = {
        {659, 200}, {0, 60},   // E
        {440, 200}, {0, 60},   // LA
        {440, 120}, {0, 40},   // ma
        {440, 120}, {0, 40},   // ma
        {0, 200},              // rest between repeats
    };
    int phraseLen = sizeof(phrase) / sizeof(phrase[0]);
    for (int i = 0; i < phraseLen; i++) {
        if (digitalRead(BTN_PIN) == LOW) { buzzOff(); return true; }
        srv.handleClient();
        if (phrase[i].freq > 0) {
            buzz(phrase[i].dur, phrase[i].freq);
        } else {
            delay(phrase[i].dur);
        }
    }
    buzzOff();
    return false;
}

// Mario Bros theme tune - plays once, returns true if button pressed
bool marioSiren() {
    struct Note { int freq; int dur; };
    Note melody[] = {
        {2637,120},{2637,120},{0,120},{2637,120},{0,120},{2093,120},{2637,120},{0,120},
        {3136,120},{0,120},{0,120},{0,240},{1568,120},{0,120},{0,120},{0,240},
    };
    int len = sizeof(melody) / sizeof(melody[0]);
    for (int i = 0; i < len; i++) {
        if (digitalRead(BTN_PIN) == LOW) { buzzOff(); return true; }
        srv.handleClient();
        if (melody[i].freq > 0) {
            buzz(melody[i].dur, melody[i].freq);
        } else {
            delay(melody[i].dur);
        }
        delay(20);
    }
    buzzOff();
    return false;
}

// Play the configured alarm, returns true if button pressed
bool playAlarm() {
    if (cfgAlarmType == 1) return marioSiren();
    return laMamaSiren();
}

void startupBeep() {
    buzz(80, 220);
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

// ===== Hebrew RTL helper =====

String reverseUTF8(const String& s) {
    std::vector<String> chars;
    const uint8_t* p = (const uint8_t*)s.c_str();
    int len = s.length();
    int i = 0;
    while (i < len) {
        int charLen;
        if (p[i] < 0x80) charLen = 1;
        else if ((p[i] & 0xE0) == 0xC0) charLen = 2;
        else if ((p[i] & 0xF0) == 0xE0) charLen = 3;
        else if ((p[i] & 0xF8) == 0xF0) charLen = 4;
        else { i++; continue; }
        if (i + charLen > len) break;
        chars.push_back(s.substring(i, i + charLen));
        i += charLen;
    }
    String result;
    for (int j = chars.size() - 1; j >= 0; j--)
        result += chars[j];
    return result;
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

// ===== Settings I/O =====

void loadSettings() {
    File f = LittleFS.open(SETTINGS_FILE, "r");
    if (!f) return;
    JsonDocument doc;
    if (deserializeJson(doc, f)) { f.close(); return; }
    f.close();
    if (doc["alarmType"].is<int>()) cfgAlarmType = doc["alarmType"].as<int>();
    if (doc["alarmRepeats"].is<int>()) { int v = doc["alarmRepeats"].as<int>(); cfgAlarmRepeats = v < 1 ? 1 : v; }
    if (doc["defaultNight"].is<bool>()) cfgDefaultNight = doc["defaultNight"].as<bool>();
    Serial.printf("Settings: alarmType=%d repeats=%d defaultNight=%d\n", cfgAlarmType, cfgAlarmRepeats, cfgDefaultNight);
}

void saveSettings() {
    JsonDocument doc;
    doc["alarmType"] = cfgAlarmType;
    doc["alarmRepeats"] = cfgAlarmRepeats;
    doc["defaultNight"] = cfgDefaultNight;
    File f = LittleFS.open(SETTINGS_FILE, "w");
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
    LittleFS.remove(SETTINGS_FILE);
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
            for (JsonVariant c : refCities) {
                monitoredCities.push_back(c.as<String>());
            }
        } else if (kv.value().is<JsonArray>()) {
            for (JsonVariant c : kv.value().as<JsonArray>()) {
                monitoredCities.push_back(c.as<String>());
            }
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

// ===== Alert State Management =====

void clearAlertState() {
    alertState = STATE_IDLE;
    alertCat = 0;
    alertTitle = "";
    alertDesc = "";
    alertMatchCity = "";
    sirenCount = 0;
    sirenSilenced = true;
    buzzOff();
}

void changeState(AlertState newState) {
    if (newState == alertState) return;  // dedup: same message -> no re-trigger
    Serial.printf("STATE: %s -> %s\n", stateLabel(alertState), stateLabel(newState));
    alertState = newState;
    stateChangedAt = millis();
    sirenCount = 0;
    sirenSilenced = false;
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
.state-box{text-align:center;padding:20px;border-radius:8px;margin:10px 0}
.state-idle{background:#16213e;border:2px solid #444}
.state-warn{background:#3d2100;border:3px solid #ff9800}
.state-shelter{background:#3d0000;border:3px solid #e94560}
.state-clear{background:#003d10;border:3px solid #2ecc71}
</style>)rawliteral";

String stateHTML() {
    String css, heb, lbl;
    switch(alertState) {
        case STATE_SHELTER: css="state-shelter"; heb="\xD7\x9C\xD7\x9E\xD7\xA7\xD7\x9C\xD7\x98!"; lbl="SHELTER"; break;
        case STATE_WARNING: css="state-warn"; heb="\xD7\x94\xD7\xAA\xD7\xA8\xD7\xA2\xD7\x94"; lbl="WARNING"; break;
        case STATE_CLEAR:   css="state-clear"; heb="\xD7\x9C\xD7\xA6\xD7\x90\xD7\xAA"; lbl="CLEAR"; break;
        default:            css="state-idle"; heb="\xD7\xAA\xD7\xA7\xD7\x99\xD7\x9F"; lbl="IDLE"; break;
    }
    return "<div class=\"state-box " + css + "\">"
        "<h1 style=\"font-size:64px;margin:0\">" + heb + "</h1>"
        "<p style=\"font-size:18px;margin:5px 0\">" + lbl + "</p></div>";
}

static const char NAV[] = R"rawliteral(<nav><a href="/">Home</a><a href="/wifi">WiFi</a><a href="/areas">Areas</a><a href="/settings">Settings</a><a href="/log">Log</a><a href="/test_page">Test</a></nav><hr>)rawliteral";

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
    String nightBtn = nightMode
        ? "<button style=\"background:#2ecc71\">Day Mode (screen on)</button>"
        : "<button style=\"background:#555\">Night Mode (screen off)</button>";
    String html = htmlHead("Emergency Alerts")
        + "<meta http-equiv=\"refresh\" content=\"5\">"
        + "<h1>Emergency Alerts</h1>" + NAV
        + stateHTML()
        + "<div class=\"card\"><h2>Status</h2>"
        + "<p>Connected to: <b>" + mySSID + "</b></p>"
        + "<p>Monitoring: <b>" + String(n) + " area(s)</b></p>"
        + "<p>Status: <b>" + (testAlertOn ? "<span style=\"color:#ff9800\">TEST MODE</span>" : (tlsOK ? "Online" : "Offline")) + "</b></p>"
        + "<p>Display: <b>" + (nightMode ? "Night" : "Day") + "</b></p>"
        + "<form method=\"POST\" action=\"/night\">" + nightBtn + "</form></div>"
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

// ===== Web Handlers: Settings =====

void handleSettings() {
    String lamamaSel = (cfgAlarmType == 0) ? " selected" : "";
    String marioSel = (cfgAlarmType == 1) ? " selected" : "";
    String nightChecked = cfgDefaultNight ? " checked" : "";
    String html = htmlHead("Settings")
        + "<h1>Settings</h1>" + NAV
        + "<form method=\"POST\" action=\"/save_settings\">"
        + "<div class=\"card\"><h2>Alarm Sound</h2>"
        + "<label>Alarm type:</label>"
        + "<select name=\"alarmType\" style=\"width:100%\">"
        + "<option value=\"0\"" + lamamaSel + ">La Mama (E-LA-mama)</option>"
        + "<option value=\"1\"" + marioSel + ">Mario (classic theme)</option>"
        + "</select></div>"
        + "<div class=\"card\"><h2>Alarm Repeats</h2>"
        + "<label>How many times to play (1 or more):</label>"
        + "<input type=\"number\" name=\"alarmRepeats\" min=\"1\" value=\"" + String(cfgAlarmRepeats) + "\" style=\"width:100%\">"
        + "</div>"
        + "<div class=\"card\"><h2>Display</h2>"
        + "<label class=\"item\"><input type=\"checkbox\" name=\"defaultNight\" value=\"1\"" + nightChecked + "> Start in Night Mode on power-on</label>"
        + "</div>"
        + "<button type=\"submit\">Save Settings</button>"
        + "</form></body></html>";
    srv.send(200, "text/html; charset=utf-8", html);
}

void handleSaveSettings() {
    cfgAlarmType = srv.arg("alarmType").toInt();
    if (cfgAlarmType < 0 || cfgAlarmType > 1) cfgAlarmType = 0;
    { int v = srv.arg("alarmRepeats").toInt(); cfgAlarmRepeats = v < 1 ? 1 : v; }
    cfgDefaultNight = srv.hasArg("defaultNight");
    saveSettings();
    Serial.printf("Settings saved: alarm=%d repeats=%d night=%d\n", cfgAlarmType, cfgAlarmRepeats, cfgDefaultNight);
    sendRedirect("/settings");
}

// ===== Web Handlers: Test =====

void handleLog() {
    srv.setContentLength(CONTENT_LENGTH_UNKNOWN);
    srv.send(200, "text/html; charset=utf-8", "");
    srv.sendContent(htmlHead("Alert Log"));
    srv.sendContent(String("<h1>Alert Log</h1>") + NAV);

    if (!LittleFS.exists(LOG_FILE)) {
        srv.sendContent("<div class=\"card\"><p>No alerts recorded yet.</p></div>");
    } else {
        File f = LittleFS.open(LOG_FILE, "r");
        if (!f || f.size() == 0) {
            if (f) f.close();
            srv.sendContent("<div class=\"card\"><p>No alerts recorded yet.</p></div>");
        } else {
            std::vector<String> lines;
            while (f.available()) {
                String line = f.readStringUntil('\n');
                line.trim();
                if (line.length() > 0) lines.push_back(line);
            }
            f.close();
            srv.sendContent("<div class=\"card\"><h2>" + String(lines.size()) + " alert(s)</h2>");
            for (int i = (int)lines.size() - 1; i >= 0; i--) {
                JsonDocument doc;
                if (deserializeJson(doc, lines[i])) continue;
                int cat = doc["cat"].as<int>();
                String title = doc["title"].as<String>();
                String city = doc["city"].as<String>();
                String desc = doc["desc"].as<String>();
                String ts = doc["time"].as<String>();
                if (ts.isEmpty()) ts = "(no time)";
                srv.sendContent("<div style=\"border-bottom:1px solid #1f2b4d;padding:8px 0\">"
                    "<b>cat=" + String(cat) + "</b> &mdash; " + ts + "<br>"
                    "<b>title:</b> " + title + "<br>"
                    "<b>city:</b> " + city + "<br>"
                    "<b>desc:</b> " + desc + "</div>");
            }
            srv.sendContent("</div>");
        }
    }
    srv.sendContent(
        "<div class=\"card\" style=\"text-align:center\">"
        "<a href=\"/log_download\" style=\"font-size:16px\">Download log file</a>"
        "<form method=\"POST\" action=\"/log_clear\" style=\"margin-top:10px\">"
        "<button style=\"background:#555\">Clear log</button></form></div>"
        "<div class=\"card\" style=\"text-align:center\">"
        "<a href=\"https://www.oref.org.il/heb/alerts-history\" target=\"_blank\" "
        "style=\"font-size:18px\">\xD7\x94\xD7\x99\xD7\xA1\xD7\x98\xD7\x95\xD7\xA8\xD7\x99\xD7\x99\xD7\xAA "
        "\xD7\x94\xD7\xAA\xD7\xA8\xD7\x90\xD7\x95\xD7\xAA</a></div>"
        "</body></html>");
    srv.sendContent("");
}

void handleLogDownload() {
    if (!LittleFS.exists(LOG_FILE)) {
        srv.send(200, "text/plain", "(empty)");
        return;
    }
    File f = LittleFS.open(LOG_FILE, "r");
    if (!f) { srv.send(200, "text/plain", "(empty)"); return; }
    srv.streamFile(f, "text/plain");
    f.close();
}

void handleLogClear() {
    LittleFS.remove(LOG_FILE);
    File f = LittleFS.open(LOG_FILE, "w");
    if (f) f.close();
    sendRedirect("/log");
}

void handleNightToggle() {
    nightMode = !nightMode;
    if (nightMode) {
        oled.clearBuffer();
        oled.sendBuffer();
    }
    sendRedirect("/");
}

void handleTestPage() {
    String status = testAlertOn ? "ACTIVE - listening for injected alerts" : "Inactive";
    String html = htmlHead("Test")
        + "<meta http-equiv=\"refresh\" content=\"5\">"
        + "<h1>Test Mode</h1>" + NAV
        + stateHTML()
        + "<div class=\"card\"><p>Test mode: <b>" + status + "</b></p>"
        + "<form method=\"POST\" action=\"/test_quick\">"
        + "<select name=\"type\" style=\"width:100%;margin-bottom:8px\">"
        + "<option value=\"warn\">\xD7\x94\xD7\xAA\xD7\xA8\xD7\xA2\xD7\x94 (Warning)</option>"
        + "<option value=\"shelter\">\xD7\x9C\xD7\x9E\xD7\xA7\xD7\x9C\xD7\x98 (Shelter)</option>"
        + "<option value=\"clear\">\xD7\x9C\xD7\xA6\xD7\x90\xD7\xAA (Clear)</option>"
        + "</select>"
        + "<button type=\"submit\" class=\"test-btn\">Quick Test</button></form>"
        + "<form method=\"POST\" action=\"/clear_test\">"
        + "<button type=\"submit\" style=\"background:#555;margin-top:5px\">Reset to Idle</button></form>"
        + "</div>"
        + "<div class=\"card\"><h2>Inject Mode</h2>"
        + "<p>1. Enter test mode, 2. Run:<br>"
        + "<code>python test_alerts.py " + myIP + "</code></p>"
        + "<form method=\"POST\" action=\"" + String(testAlertOn ? "/clear_test" : "/test") + "\">"
        + "<button type=\"submit\" style=\"" + String(testAlertOn ? "background:#555" : "background:#ff9800") + "\">" + (testAlertOn ? "End Inject Mode" : "Enter Inject Mode") + "</button></form>"
        + "</div>"
        + "<div class=\"card\"><form action=\"/reset_all\" method=\"POST\">"
        + "<button style=\"background:#c0392b\">Factory Reset</button>"
        + "</form></div></body></html>";
    srv.send(200, "text/html; charset=utf-8", html);
}

void handleTest() {
    testAlertOn = true;
    testHasInjection = false;
    testInjectedBody = "";
    clearAlertState();
    sendRedirect("/test_page");
}

void handleClearTest() {
    testAlertOn = false;
    testHasInjection = false;
    testInjectedBody = "";
    clearAlertState();
    sendRedirect("/test_page");
}

void handleTestQuick() {
    String type = srv.arg("type");

    // Use first monitored city
    if (monitoredCities.empty()) {
        srv.send(400, "text/plain", "No monitored cities configured");
        return;
    }
    String city = monitoredCities[0];

    AlertState newState = STATE_IDLE;
    if (type == "shelter") {
        alertTitle = "\xD7\x99\xD7\xA8\xD7\x99 \xD7\xA8\xD7\xA7\xD7\x98\xD7\x95\xD7\xAA \xD7\x95\xD7\x98\xD7\x99\xD7\x9C\xD7\x99\xD7\x9D";
        alertDesc = "\xD7\x94\xD7\x99\xD7\x9B\xD7\xA0\xD7\xA1\xD7\x95 \xD7\x9C\xD7\x9E\xD7\xA8\xD7\x97\xD7\x91 \xD7\x94\xD7\x9E\xD7\x95\xD7\x92\xD7\x9F";
        alertCat = 1;
        newState = STATE_SHELTER;
    } else if (type == "clear") {
        alertTitle = "\xD7\x94\xD7\x90\xD7\x99\xD7\xA8\xD7\x95\xD7\xA2 \xD7\x94\xD7\xA1\xD7\xAA\xD7\x99\xD7\x99\xD7\x9D";
        alertDesc = "\xD7\x94\xD7\xA9\xD7\x95\xD7\x94\xD7\x99\xD7\x9D \xD7\x91\xD7\x9E\xD7\xA8\xD7\x97\xD7\x91 \xD7\x94\xD7\x9E\xD7\x95\xD7\x92\xD7\x9F \xD7\x99\xD7\x9B\xD7\x95\xD7\x9C\xD7\x99\xD7\x9D \xD7\x9C\xD7\xA6\xD7\x90\xD7\xAA";
        alertCat = 10;
        newState = STATE_CLEAR;
    } else {
        alertTitle = "\xD7\x91\xD7\x93\xD7\xA7\xD7\x95\xD7\xAA \xD7\x94\xD7\xA7\xD7\xA8\xD7\x95\xD7\x91\xD7\x95\xD7\xAA \xD7\xA6\xD7\xA4\xD7\x95\xD7\x99\xD7\x95\xD7\xAA \xD7\x9C\xD7\x94\xD7\xAA\xD7\xA7\xD7\x91\xD7\x9C \xD7\x94\xD7\xAA\xD7\xA8\xD7\xA2\xD7\x95\xD7\xAA \xD7\x91\xD7\x90\xD7\x96\xD7\x95\xD7\xA8\xD7\x9A";
        alertDesc = "\xD7\xA2\xD7\x9C \xD7\xAA\xD7\x95\xD7\xA9\xD7\x91\xD7\x99 \xD7\x94\xD7\x90\xD7\x96\xD7\x95\xD7\xA8\xD7\x99\xD7\x9D \xD7\x94\xD7\x91\xD7\x90\xD7\x99\xD7\x9D \xD7\x9C\xD7\xA9\xD7\xA4\xD7\xA8 \xD7\x90\xD7\xAA \xD7\x94\xD7\x9E\xD7\x99\xD7\xA7\xD7\x95\xD7\x9D \xD7\x9C\xD7\x9E\xD7\x99\xD7\x92\xD7\x95\xD7\x9F \xD7\x94\xD7\x9E\xD7\x99\xD7\x98\xD7\x91\xD7\x99 \xD7\x91\xD7\xA7\xD7\xA8\xD7\x91\xD7\xAA\xD7\x9A";
        alertCat = 10;
        newState = STATE_WARNING;
    }
    alertMatchCity = city;
    changeState(newState);
    addAlertLog(alertCat, alertTitle, alertDesc, alertMatchCity);
    sendRedirect("/test_page");
}

void handleTestInject() {
    if (!testAlertOn) {
        srv.send(403, "application/json", "{\"error\":\"Test mode not active\"}");
        return;
    }
    if (!srv.hasArg("plain")) {
        srv.send(400, "application/json", "{\"error\":\"No JSON body\"}");
        return;
    }
    testInjectedBody = srv.arg("plain");
    testHasInjection = true;
    Serial.println("Test inject: " + testInjectedBody);
    srv.send(200, "application/json", "{\"ok\":true}");
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

// Returns: 0 = no alert, >0 = alert category for monitored city
int pollAlerts() {
    if (monitoredCities.empty()) return 0;

    // Reconnect if needed
    if (!tlsClient.connected()) {
        tlsOK = false;
        connectTLS();
        if (!tlsOK) return 0;
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
            return 0;
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
    if (body.isEmpty() || body == "\"\"" || body == "{}" || body == "[]") return 0;

    // Remove BOM
    if (body.length() > 2 && (uint8_t)body.charAt(0) == 0xEF
        && (uint8_t)body.charAt(1) == 0xBB && (uint8_t)body.charAt(2) == 0xBF) {
        body = body.substring(3);
        body.trim();
    }
    if (body.isEmpty()) return 0;

    JsonDocument doc;
    if (deserializeJson(doc, body)) return 0;
    if (!doc["data"].is<JsonArray>() || doc["data"].as<JsonArray>().size() == 0) return 0;

    // Extract cat, title, desc
    int cat = doc["cat"].as<int>();
    if (cat == 0 && doc["cat"].is<const char*>())
        cat = atoi(doc["cat"].as<const char*>());
    String title = doc["title"].as<String>();
    String desc = doc["desc"].as<String>();

    for (JsonVariant ac : doc["data"].as<JsonArray>()) {
        String city = ac.as<String>();
        for (int i = 0; i < (int)monitoredCities.size(); i++) {
            if (city.indexOf(monitoredCities[i]) >= 0 || monitoredCities[i].indexOf(city) >= 0) {
                alertMatchCity = monitoredCities[i];
                alertTitle = title;
                alertDesc = desc;
                alertCat = cat;
                Serial.printf("ALERT MATCH cat=%d: %s -> %s\n", cat, city.c_str(), alertMatchCity.c_str());
                Serial.println("  title: " + title);
                Serial.println("  desc:  " + desc);
                return cat;
            }
        }
    }
    return 0;
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
    oled.begin();
    oled.clearBuffer();
    oled.sendBuffer();
    initBuzzer();
    buzzOff();
    pinMode(BTN_PIN, INPUT_PULLUP);

    // Filesystem
    if (!LittleFS.begin(true)) {
        Serial.println("LittleFS mount failed!");
        show6("FS ERROR!", "", "Reflash device");
        while (1) delay(1000);
    }
    // Ensure log file exists so reads don't trigger VFS errors
    if (!LittleFS.exists(LOG_FILE)) {
        File f = LittleFS.open(LOG_FILE, "w");
        if (f) f.close();
    }

    // Load user settings and apply default night mode
    loadSettings();
    nightMode = cfgDefaultNight;

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

    // Sync time via NTP
    initNTP();

    // Establish TLS before web server
    show6("TLS handshake...");
    connectTLS();
    if (tlsOK) {
        show6("Connected!", ("IP: " + myIP).c_str(),
              ("WiFi: " + mySSID).c_str(), "", "Online");
    } else {
        show6("Connected!", ("IP: " + myIP).c_str(),
              ("WiFi: " + mySSID).c_str(), "", "Offline", "Will retry...");
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
    srv.on("/settings", handleSettings);
    srv.on("/save_settings", HTTP_POST, handleSaveSettings);
    srv.on("/log", handleLog);
    srv.on("/log_download", handleLogDownload);
    srv.on("/log_clear", HTTP_POST, handleLogClear);
    srv.on("/night", HTTP_POST, handleNightToggle);
    srv.on("/test_page", handleTestPage);
    srv.on("/test", HTTP_POST, handleTest);
    srv.on("/test_quick", HTTP_POST, handleTestQuick);
    srv.on("/test_inject", HTTP_POST, handleTestInject);
    srv.on("/clear_test", HTTP_POST, handleClearTest);
    srv.on("/reset_all", HTTP_POST, handleResetAll);
    srv.on("/favicon.ico", []() { srv.send(204); });
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

    unsigned long now = millis();

    // ===== 1. Poll / process alerts =====
    int cat = 0;

    if (testAlertOn && testHasInjection) {
        // Process test injections IMMEDIATELY (don't wait for poll timer)
        testHasInjection = false;
        String body = testInjectedBody;
        body.trim();
        if (body.isEmpty() || body == "\"\"" || body == "{}" || body == "[]") {
            if (alertState != STATE_IDLE) {
                Serial.println("Test inject: empty payload -> IDLE");
                clearAlertState();
            }
        } else {
            JsonDocument doc;
            if (!deserializeJson(doc, body)
                && doc["data"].is<JsonArray>()
                && doc["data"].as<JsonArray>().size() > 0) {
                int c = doc["cat"].as<int>();
                if (c == 0 && doc["cat"].is<const char*>())
                    c = atoi(doc["cat"].as<const char*>());
                String title = doc["title"].as<String>();
                String desc = doc["desc"].as<String>();

                // Match against monitored cities (same as pollAlerts)
                for (JsonVariant ac : doc["data"].as<JsonArray>()) {
                    String city = ac.as<String>();
                    for (int i = 0; i < (int)monitoredCities.size(); i++) {
                        if (city.indexOf(monitoredCities[i]) >= 0 || monitoredCities[i].indexOf(city) >= 0) {
                            alertMatchCity = monitoredCities[i];
                            alertTitle = title;
                            alertDesc = desc;
                            alertCat = c;
                            cat = c;
                            Serial.printf("Test inject MATCH cat=%d: %s -> %s\n", c, city.c_str(), alertMatchCity.c_str());
                            break;
                        }
                    }
                    if (cat > 0) break;
                }
                if (cat == 0) {
                    Serial.printf("Test inject: no city match (cat=%d title=%s)\n", c, title.c_str());
                }
            }
        }
    } else if (!testAlertOn && (now - lastPoll > POLL_MS)) {
        // Normal mode: poll API on timer
        lastPoll = now;
        cat = pollAlerts();
    }

    if (cat > 0) {
        AlertState newState = classifyAlert(alertTitle);
        if (newState == STATE_IDLE) {
            if ((cat >= 1 && cat <= 7) || cat == 13) newState = STATE_SHELTER;
            else if (cat == 10) newState = STATE_WARNING;
        }
        Serial.printf("Classified: cat=%d -> %s (current: %s)\n", cat, stateLabel(newState), stateLabel(alertState));

        if (newState != STATE_IDLE && newState != alertState) {
            addAlertLog(cat, alertTitle, alertDesc, alertMatchCity);
            changeState(newState);
        }
    }

    // ===== 2. Auto-clear CLEAR state after 60 seconds =====
    if (alertState == STATE_CLEAR) {
        if (now - stateChangedAt > CLEAR_TIMEOUT_MS) {
            Serial.println("CLEAR expired -> IDLE");
            clearAlertState();
        }
    }

    // ===== 3. Display =====
    if (alertState == STATE_SHELTER) {
        oled.clearBuffer();
        oled.drawXBM(0, 0, 128, 64, bmp_lamiklat);
        oled.sendBuffer();
    } else if (alertState == STATE_WARNING) {
        if ((now / 500) % 2 == 0) {
            oled.clearBuffer();
            oled.drawXBM(0, 0, 128, 64, bmp_hatraa);
            oled.sendBuffer();
        } else {
            oled.clearBuffer();
            oled.sendBuffer();
        }
    } else if (alertState == STATE_CLEAR) {
        oled.clearBuffer();
        oled.drawXBM(0, 0, 128, 64, bmp_latzet);
        oled.sendBuffer();
    } else if (nightMode) {
        oled.clearBuffer();
        oled.sendBuffer();
    } else {
        unsigned long now2 = millis();
        if (now2 - lastDisplaySwitch > 4000) {
            lastDisplaySwitch = now2;
            if (!monitoredCities.empty()) {
                showCityList = !showCityList;
                if (showCityList) cityScrollOffset += 3;
                if (cityScrollOffset >= (int)monitoredCities.size()) cityScrollOffset = 0;
            }
        }
        if (showCityList && !monitoredCities.empty()) {
            oled.clearBuffer();
            oled.setFont(u8g2_font_6x10_tf);
            oled.drawStr(0, 10, "Monitoring:");
            oled.setFont(u8g2_font_unifont_t_hebrew);
            for (int i = 0; i < 3 && (cityScrollOffset + i) < (int)monitoredCities.size(); i++) {
                String rev = reverseUTF8(monitoredCities[cityScrollOffset + i]);
                int w = oled.getUTF8Width(rev.c_str());
                oled.drawUTF8(128 - w, 28 + i * 16, rev.c_str());
            }
            oled.sendBuffer();
        } else {
            int n = monitoredCities.size();
            String status = n > 0 ? String(n) + " cities" : "Not set";
            String online = testAlertOn ? "TEST MODE" : (tlsOK ? "Online" : "Offline");
            show6("Emergency Alert", ("IP: " + myIP).c_str(), "",
                  "Monitoring:", status.c_str(), online.c_str());
        }
    }

    // ===== 4. Sound: alarm for SHELTER (configurable), beep for CLEAR =====
    if (alertState == STATE_SHELTER && !sirenSilenced) {
        if (sirenCount < cfgAlarmRepeats) {
            bool pressed = playAlarm();
            if (pressed) {
                sirenSilenced = true;
                buzzOff();
                while (digitalRead(BTN_PIN) == LOW) delay(50);
                delay(300);
            } else {
                sirenCount++;
                if (sirenCount >= cfgAlarmRepeats) {
                    sirenSilenced = true;
                }
            }
        } else {
            delay(100);
        }
    } else if (alertState == STATE_CLEAR && !sirenSilenced) {
        buzz(200, 880);
        delay(100);
        buzz(200, 880);
        sirenSilenced = true;
    } else {
        delay(100);
    }

    // ===== 5. Button: just stop buzzer (no state change) =====
    if (digitalRead(BTN_PIN) == LOW && !sirenSilenced) {
        if (alertState == STATE_SHELTER) {
            sirenSilenced = true;
            buzzOff();
            while (digitalRead(BTN_PIN) == LOW) delay(50);
            delay(300);
        }
    }
}
