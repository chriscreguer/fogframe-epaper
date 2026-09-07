// Board/screen selection for Seeed_GFX — XIAO ePaper Display Board EE02
// driving the 13.3" Spectra-6 (E6 / T133A01, 1200x1600, 6 colour) panel.
//
// These three defines must come BEFORE including TFT_eSPI.h.
#define BOARD_SCREEN_COMBO 510          // 13.3" six-colour ePaper (T133A01)
#define ENABLE_EPAPER_BOARD_PIN_SETUPS  // use a board-specific pin map...
#define USE_XIAO_EPAPER_DISPLAY_BOARD_EE02  // ...the EE02 one
//
// If the panel never responds, the official EE02 wiki uses only the first and
// third lines (falling back to the Setup510 default pins). Try removing the
// middle line in that case.
