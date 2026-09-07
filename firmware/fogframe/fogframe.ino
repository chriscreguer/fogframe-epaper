/*
 * fogframe — XIAO ESP32-S3 (on the Seeed EE02 board) driving a 13.3" Spectra-6
 * e-paper panel. On each boot it pulls a pre-packed 6-colour image over
 * Wi-Fi, paints it, and deep-sleeps until the next reset/power cycle.
 *
 * The image (device/frame.bin) is the RAW T133A01 4bpp buffer: 1200x1600,
 * 2 px/byte, high nibble = even (left) pixel, nibble = panel colour code
 * (0x0=W 0x2=G 0x6=R 0xB=Y 0xD=B 0xF=BK). We stream it straight into the
 * Seeed_GFX sprite buffer via drawBufferPixel() — no on-device decoding.
 *
 * Build (Arduino IDE):
 *   Board:  "XIAO_ESP32S3" (the Plus variant)
 *   PSRAM:  ENABLED   (Tools -> PSRAM: "OPI PSRAM")   <-- required, 960 KB buffer
 *   Library: Seeed_GFX installed
 *
 * On any Wi-Fi/download failure it does NOT repaint, so the panel keeps its
 * previous image (e-paper holds without power) — but it does NOT give up:
 * a failed boot wakes again on a short timer and retries, so a flaky moment
 * can't strand a stale frame until someone unplugs the thing.
 */
#include "driver.h"
#include "TFT_eSPI.h"
#include "esp_sleep.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>

// ---- user config ------------------------------------------------------------
#include "secrets.h"   // WIFI_SSID / WIFI_PASS / IMG_URL — see secrets.h.example

static const uint32_t WIFI_TIMEOUT_MS = 15000;          // per attempt
static const int      WIFI_MAX_TRIES  = 6;              // per boot, before retrying later
static const uint64_t RETRY_SECONDS   = 15ULL * 60;     // wait between failed boots
static const int      MAX_RETRIES     = 8;              // ~2h of retries, then give up
// -----------------------------------------------------------------------------

static const int    PANEL_W    = 1200;
static const int    ROW_BYTES  = PANEL_W / 2;            // 600
static const size_t FRAME_BYTES = (size_t)ROW_BYTES * 1600;  // 960000

EPaper epaper;

// Deep sleep preserves RTC memory; a real power cycle clears it. That is exactly
// the lifetime we want for the retry budget — every plug-on starts fresh.
RTC_DATA_ATTR static int retriesUsed = 0;

static void deepSleepUntilPowerCycle() {
  WiFi.disconnect(true);
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);
  Serial.println("Sleeping until next reset/power cycle...");
  Serial.flush();
  esp_deep_sleep_start();
}

// A boot that never painted leaves yesterday's frame on the panel, so sleeping
// until the next power cycle would strand it there indefinitely. Wake again
// shortly instead and retry, up to a bounded budget.
static void deepSleepAndRetry() {
  if (retriesUsed >= MAX_RETRIES) {
    Serial.printf("Gave up after %d retries.\n", retriesUsed);
    deepSleepUntilPowerCycle();       // does not return
  }
  retriesUsed++;
  WiFi.disconnect(true);
  esp_sleep_enable_timer_wakeup(RETRY_SECONDS * 1000000ULL);
  Serial.printf("Retry %d/%d in %llus...\n", retriesUsed, MAX_RETRIES,
                (unsigned long long)RETRY_SECONDS);
  Serial.flush();
  esp_deep_sleep_start();
}

// Returns true only if the full frame downloaded and was painted.
static bool fetchAndPaint() {
  WiFiClientSecure client;
  client.setInsecure();                 // skip cert validation (hobby use)
  HTTPClient http;
  http.setTimeout(20000);
  if (!http.begin(client, IMG_URL)) { Serial.println("http.begin failed"); return false; }

  int code = http.GET();
  if (code != HTTP_CODE_OK) { Serial.printf("HTTP %d\n", code); http.end(); return false; }

  int len = http.getSize();             // expect 960000 (or -1 if chunked)
  Serial.printf("Content-Length: %d\n", len);
  WiFiClient* stream = http.getStreamPtr();

  uint8_t buf[1460];
  size_t idx = 0;
  uint32_t lastData = millis();
  while (http.connected() && idx < FRAME_BYTES) {
    size_t avail = stream->available();
    if (avail) {
      int r = stream->readBytes(buf, min(avail, sizeof(buf)));
      for (int i = 0; i < r && idx < FRAME_BYTES; ++i, ++idx) {
        int y  = idx / ROW_BYTES;
        int bx = idx % ROW_BYTES;
        epaper.drawBufferPixel(bx * 2, y, buf[i], 4);  // 2 px per byte
      }
      lastData = millis();
    } else {
      if (millis() - lastData > 15000) { Serial.println("stream stalled"); break; }
      delay(2);
    }
  }
  http.end();
  Serial.printf("Received %u / %u bytes\n", (unsigned)idx, (unsigned)FRAME_BYTES);
  if (idx < FRAME_BYTES) return false;

  epaper.update();                      // ~25-35s full refresh, then panel sleeps
  return true;
}

// On boot: scan + list visible networks (handy if WiFi ever breaks again) and
// print disconnect reasons. Tries to connect up to WIFI_MAX_TRIES times (your
// signal is marginal and often needs a couple attempts), then either paints and
// sleeps until the next power cycle, or — if it never connects — keeps the old
// image and schedules a retry.
static const char* wifiReason(uint8_t r) {
  switch (r) {
    case 2:   return "AUTH_EXPIRE";
    case 15:  return "4WAY_HANDSHAKE_TIMEOUT (usually WRONG PASSWORD)";
    case 200: return "BEACON_TIMEOUT (weak/lost signal)";
    case 201: return "NO_AP_FOUND (too weak / not in range)";
    case 202: return "AUTH_FAIL (WRONG PASSWORD)";
    case 203: return "ASSOC_FAIL";
    case 204: return "HANDSHAKE_TIMEOUT (weak signal)";
    case 205: return "CONNECTION_FAIL";
    default:  return "(see esp_wifi_types.h)";
  }
}

static void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    uint8_t r = info.wifi_sta_disconnected.reason;
    Serial.printf("  [event] STA disconnected, reason=%u  %s\n", r, wifiReason(r));
  }
}

static void scanAndList() {
  Serial.println("Scanning for 2.4GHz networks (ESP32-S3 cannot see 5GHz)...");
  int n = WiFi.scanNetworks();
  Serial.printf("Found %d network(s):\n", n);
  bool sawTarget = false;
  for (int i = 0; i < n; i++) {
    bool match = WiFi.SSID(i) == String(WIFI_SSID);
    if (match) sawTarget = true;
    Serial.printf("  %2d: '%s'%s  RSSI=%ddBm  ch=%d  enc=%d\n",
                  i, WiFi.SSID(i).c_str(), match ? "  <-- TARGET" : "",
                  WiFi.RSSI(i), WiFi.channel(i), (int)WiFi.encryptionType(i));
  }
  Serial.printf("Target SSID '%s' %s in scan.\n",
                WIFI_SSID, sawTarget ? "FOUND" : "NOT FOUND (wrong name or 5GHz-only)");
}

void setup() {
  Serial.begin(115200);
  delay(400);
  Serial.printf("\nfogframe waking (debug build), retry %d/%d\n", retriesUsed, MAX_RETRIES);

  epaper.begin();                       // allocates the 960 KB 4bpp sprite (PSRAM)

  WiFi.mode(WIFI_STA);
  WiFi.onEvent(onWiFiEvent);             // prints exact disconnect reason
  WiFi.disconnect(true);
  delay(100);
  scanAndList();

  for (int attempt = 1; attempt <= WIFI_MAX_TRIES; attempt++) {
    Serial.printf("Connecting to '%s' (attempt %d/%d)...\n", WIFI_SSID, attempt, WIFI_MAX_TRIES);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_TIMEOUT_MS) delay(250);

    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("WiFi ok: %s\n", WiFi.localIP().toString().c_str());
      if (fetchAndPaint()) {
        Serial.println("painted new frame");
        deepSleepUntilPowerCycle();   // does not return
      }
      Serial.println("fetch failed — keeping previous image");
      deepSleepAndRetry();            // does not return
    }

    Serial.printf("WiFi attempt %d failed (status=%d).\n", attempt, WiFi.status());
    if (attempt < WIFI_MAX_TRIES) { WiFi.disconnect(true); delay(4000); }
  }

  Serial.println("WiFi unreachable — keeping previous image.");
  deepSleepAndRetry();
}

void loop() {}
