/*
 * screentest — verifies the 13.3" Spectra-6 panel is wired/talking to the
 * XIAO ESP32-S3 (EE02 board). No WiFi involved: paints six full-width color
 * bars (white, yellow, red, green, blue, black) and stops.
 *
 * Build exactly like fogframe.ino:
 *   Board:  "XIAO_ESP32S3" (Plus variant)
 *   PSRAM:  ENABLED (Tools -> PSRAM: "OPI PSRAM")
 *   Library: Seeed_GFX
 *
 * Expected: serial prints each step, then a ~25-35s refresh cycle (panel
 * flashes through its update sequence) ending with the six bars. If the
 * panel stays blank or unchanged, the ESP32<->panel connection is bad —
 * see the note in driver.h about the pin-setup defines.
 */
#include "driver.h"
#include "TFT_eSPI.h"

static const int PANEL_W = 1200;
static const int PANEL_H = 1600;

// Panel nibble codes, one bar each: W, Y, R, G, B, BK
static const uint8_t BAR_COLORS[] = { 0x0, 0xB, 0x6, 0x2, 0xD, 0xF };
static const int NUM_BARS = sizeof(BAR_COLORS);

EPaper epaper;

void setup() {
  Serial.begin(115200);
  delay(400);
  Serial.println("\nscreentest: init panel...");

  epaper.begin();
  Serial.println("screentest: panel init ok, filling color bars...");

  for (int y = 0; y < PANEL_H; y++) {
    uint8_t c = BAR_COLORS[min(y / (PANEL_H / NUM_BARS), NUM_BARS - 1)];
    uint8_t packed = (c << 4) | c;              // 2 px per byte, same color
    for (int bx = 0; bx < PANEL_W / 2; bx++) {
      epaper.drawBufferPixel(bx * 2, y, packed, 4);
    }
  }

  Serial.println("screentest: buffer ready, refreshing panel (~30s)...");
  epaper.update();
  Serial.println("screentest: DONE — you should see 6 horizontal bars:");
  Serial.println("  white / yellow / red / green / blue / black (top to bottom)");
}

void loop() { delay(1000); }
