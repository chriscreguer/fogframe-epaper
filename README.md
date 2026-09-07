# fogframe

Turns [Fog of World](https://fogofworld.app/) exploration data into a daily
image on a 13.3" Spectra-6 e-paper frame — regenerated in the cloud, no
computer required.

```
 Phone (Fog of World) ──sync──▶ Dropbox / a local folder
                                   │
        GitHub Actions (daily cron, runs on GitHub's servers)
          fetch data ─▶ append world delta ─▶ render ─▶ quantise to 6 colours
                                   │
                         commit device/frame.bin
                                   │
   XIAO ESP32-S3 + EE02 board ──power on──▶ fetch frame.bin ─▶ paint ─▶ sleep
                                   │
                       13.3" Spectra-6 e-paper panel
```

Everything location-specific lives in `config.toml`. Point it at your own
bounding box, bake a base map, and it renders your city instead.

## ⚠️ This publishes a map of where you have been

The pipeline commits `device/frame.bin`, `device/preview.png` and the `world/`
archive to your repository. Together those are a day-by-day record of the
streets you have walked, and in a public repo anyone can read them.

**Run your instance in a private repo.** The cost is that the panel then needs
an authenticated URL to fetch its frame — see
[Keeping your data private](#keeping-your-data-private).

`config.toml` (which holds your bounding box) is gitignored by default.

## Pieces

| File | Job |
|------|-----|
| `config.py` / `config.example.toml` | Every tunable: bounds, colours, panel, data source |
| `geo.py` | Map bounds + the Web Mercator projection, shared by everything |
| `parser.py` | Decode Fog of World sync tiles (from CaviarChen's parser) |
| `sources.py` | Resolve the data source: a local folder or Dropbox |
| `dropbox_pull.py` | Download the Sync folder via a Dropbox refresh token |
| `dropbox_auth.py` | One-time helper to obtain that refresh token |
| `worldlog.py` | Whole-world delta archive: scan, append, replay, derive |
| `snapshot.py` | Append today's delta |
| `make_base_map.py` | Bake `assets/map_base.png` for your bbox from XYZ tiles |
| `render_fog.py` | Composite fog over the base map → RGB canvas |
| `pack_spectra6.py` | Quantise to the 6 Spectra colours → `device/frame.bin` |
| `build.py` | Run the whole pipeline (used by CI) |
| `.github/workflows/daily.yml` | The daily cloud job |
| `firmware/fogframe/` | XIAO ESP32-S3 firmware for the EE02 board |

`device/frame.bin` is the RAW T133A01 4bpp buffer (1200×1600, 2 px/byte, high
nibble = even pixel, nibble = panel colour code
`0x0=W 0x2=G 0x6=R 0xB=Y 0xD=B 0xF=BK`). The firmware streams it straight into
the Seeed_GFX sprite — no on-device decoding.

## What you need

There is no service to host and nothing of mine to depend on.

| | Needed? | Cost |
|---|---|---|
| The Fog of World app | yes — it is the data source | the app |
| A GitHub account | only for the daily cloud build | free (public repo = free Actions) |
| Map tiles | no account; OpenStreetMap's public server | free |
| A Dropbox app | **only** for the cloud build | free, one-time setup |
| XIAO ESP32-S3 + EE02 + 13.3" Spectra-6 panel | yes | the real cost |

**Running locally needs no accounts at all** — set `[source] kind = "local"`,
point it at your synced Fog of World folder and run `build.py` yourself.

Dropbox is only needed because GitHub's runners cannot see your machine, so a
headless daily build needs its own way to read the data. If you would rather
not create a Dropbox app, run the build on a machine of your own on a cron and
push the result.

## Setup

### 1. Configure

```bash
pip install -r requirements.txt
cp config.example.toml config.toml
```

Edit `config.toml`. The parts that matter first:

- `[map]` — your bounding box in decimal degrees. It is expanded automatically
  to the canvas aspect ratio, so it need not be 3:4 itself.
- `[source]` — `kind = "local"` plus a `path` to a folder containing `Sync/`,
  or `kind = "dropbox"` for headless runs.
- `[basemap] user_agent` — a real contact address; public tile servers require one.

**Choose your bounds before you start collecting.** They are baked into the
base map, and moving them means re-baking it.

### 2. Bake the base map

```bash
python make_base_map.py
```

This fetches raster tiles for your bbox, stitches, crops and downsamples them
to `assets/map_base.png`.

Alignment is exact rather than eyeballed: Fog of World's global pixel grid is
4,194,304 px across, which is precisely the standard XYZ tile grid at zoom 14,
so world pixels *are* tile pixels and the script reuses `geo.py`'s projection
rather than reimplementing it.

It also prints the dominant hues it found — see
[Water detection](#water-detection) if you change tile styles.

### 3. Try it locally

```bash
python build.py --data "/path/to/Fog of World"
```

Check `device/preview.png`.

### 4. Run it daily in the cloud

Skip this if you are building locally.

Access tokens expire after a few hours, which is no good for a cron job, so the
build uses a non-expiring *refresh* token and mints short-lived access tokens
from it. To get one:

```bash
python dropbox_auth.py
```

It prints the exact steps for creating the app (free, ~2 minutes), walks the
authorise flow, and hands back the three values to paste into
Settings → Secrets and variables → Actions:

| Name | What it is |
|------|------------|
| `DROPBOX_APP_KEY` | from your app's Settings tab |
| `DROPBOX_APP_SECRET` | from the same place |
| `DROPBOX_REFRESH_TOKEN` | produced by `dropbox_auth.py`; does not expire |

Grant `files.metadata.read` and `files.content.read` **before** authorising, or
the token comes back without the scopes and downloads fail.

Then Actions → **daily-frame** → *Run workflow*.

> Scheduled runs are deferred by GitHub, historically by hours rather than
> minutes. If you want the frame fresh at a specific time, run every few hours
> rather than trying to guess the lag.

### 5. Flash the firmware

- Arduino IDE: install **Seeed_GFX** (remove TFT_eSPI if present — they conflict).
- `cp firmware/fogframe/secrets.h.example firmware/fogframe/secrets.h` and fill
  in Wi-Fi and `IMG_URL`. `secrets.h` is gitignored.
- Board: **XIAO_ESP32S3**, and set **Tools → PSRAM: OPI PSRAM** — required, the
  frame buffer is 960 KB.
- Upload. The panel refreshes (~25–35 s) then sleeps until the next power cycle.
  Put the frame on a smart plug schedule to control refreshes.
- On Wi-Fi or download failure it keeps the previous image and retries every
  15 min (up to 8 times), so a flaky moment does not strand a stale frame.

> If the panel does not refresh after an upload, check **PSRAM: OPI PSRAM**
> first. Arduino resets it on board-package updates, and without it the 960 KB
> buffer cannot be allocated — the sketch boots but never paints.

## The world archive

`world/` is an append-only record of every block Fog of World has reported
explored — the whole planet, not just your bbox. Each day writes one
`world/YYYY-MM-DD.npz` holding only the blocks that changed and only the bits
that turned on. A typical day is **under a kilobyte**.

The rolling 30-day "recently explored" tint is **derived** from the archive at
render time rather than stored. Two things follow:

- **Changing your bbox does not destroy history.** The archive is in global
  coordinates, so new bounds simply re-derive from the same data.
- **Any past date can be re-rendered** by replaying the log up to that day.

Replay is *additive*: it ORs new bits and ignores recorded removals, so the
archive accumulates the union of everything ever reported and acts as a durable
record rather than a mirror of Fog of World's current state. Removals are still
written to each delta, so a quiet day can be told apart from a sync that
dropped data.

One archive per Fog of World database — they are never merged. `meta.json`
records which database a log belongs to.

## Keeping your data private

The recommended setup is a **private repo**. The panel then cannot fetch
`frame.bin` from an anonymous `raw.githubusercontent.com` URL. Options:

1. **A fine-grained PAT** with read-only Contents access to that one repo, sent
   as an `Authorization: token …` header from the firmware. Simplest; the token
   lives in `secrets.h`, which is gitignored.
2. **Publish only the frame.** Keep the repo private and have the workflow push
   just `device/frame.bin` to a separate public repo or a release asset. The
   image alone still shows your explored streets, so this is a smaller leak,
   not no leak.
3. **Host it yourself** anywhere the panel can reach over HTTPS.

## Tuning

### Water detection
Water is found in the base map **by hue**, not from map data, so it depends on
your tile style. `[water] hue_lo/hue_hi/sat_min` defaults suit OSM Carto.
`make_base_map.py` prints the dominant saturated hues in your baked map — if
water is being missed or land is being flooded, set the range from that output.

### Look
`[colors]` sets the fog overlay, its opacity, the recent tint and the two water
colours. `[render] recent_days` controls how long new exploration stays tinted;
`fringe_px` and `min_cluster_px` suppress GPS jitter at the edge of already-
explored streets being read as new.

### Panel
`[panel] sat_boost` and `sat_threshold` control how the muted base map is
pushed into the six available colours before packing. Raise `sat_boost` for a
punchier frame, raise `sat_threshold` to keep more of the map neutral.

## Credits

`parser.py` is derived from [CaviarChen/fog-machine](https://github.com/CaviarChen/fog-machine)
and remains the work of its original author. Everything else is MIT — see
[LICENSE](LICENSE).

Base map tiles come from whatever `[basemap] tile_url` points at; respect that
provider's usage policy and attribution requirements.
