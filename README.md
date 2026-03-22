# 🌍 G E O S C O U T 🌍

**Image Geolocation Through Street-Level Comparison**

*by Eph at ephemeradev.net · est. 2026 · Open Source 💜*

---

Upload a photo. Draw a search area on a map. Find exactly where it was taken.

Four imagery sources, four comparison algorithms, zero guesswork. Built for investigators, journalists, and researchers who need to verify locations from imagery and don't have time to scroll through Google Maps squinting at screenshots.

---

## ✦ How It Works

You have an image. Maybe it's from a social media post, a screenshot from a video, a photo someone sent you, whatever. You don't know where it was taken, but you have a general idea of the region.

1. Upload the image
2. Draw a rectangle on the map covering your search area
3. Pick a provider (Mapillary, Google Street View, Baidu, or satellite)
4. Hit scan

GeoScout pulls existing street-level or satellite imagery from across that area and runs **four comparison algorithms** against your reference photo: perceptual hashing for structural similarity, SSIM for luminance and contrast, color histogram matching for color profiles, and template matching for specific visual features. Each method gets a weighted score. Anything above your threshold shows up as a pin on the map with a direct link to view the matched image at that exact location and heading.

It does two passes. Coarse grid first to find hot zones, then a fine grid around anything promising to narrow down to building-level precision. You watch it work in real time.

---

## ✦ Search Modes

**Mapillary** · Free, Global

> Crowdsourced street-level imagery with 2+ billion photos. Free API token, no cost. Best coverage in Western cities and Europe. This is the default for most searches and honestly it's kind of insane that it's free.

**Google Street View** · Paid, No China

> Highest quality imagery available. Two-pass scan with configurable headings (N/S/E/W on coarse, all 8 cardinal directions on fine). $7 per 1,000 images, but Google gives you $200/month free credit, so roughly 28,000 images before you pay a cent.

**Baidu Panorama** · Free Tier, China

> Best coverage of Chinese cities by a mile. Handles WGS84 to BD09 coordinate conversion automatically so you don't have to think about China's coordinate offset bullshit. Requires a Baidu developer key.

**Satellite** · Free, Overhead

> Overhead imagery comparison for aerial and satellite photos. Pulls from ESRI World Imagery, Sentinel-2, or Google Satellite tiles. Configurable zoom levels from region scale down to individual buildings. Coarse pass finds the neighborhood; fine pass finds the roof.

---

## ✦ Features

**4 Comparison Algorithms**

> pHash (perceptual hashing), SSIM (structural similarity index), color histogram intersection, and normalized cross-correlation template matching. Each one catches different things. You can toggle them on/off and adjust their weights depending on what your reference image looks like.

**Interactive Map with Draw-to-Search**

> Leaflet map with satellite and street base layers. Draw a rectangle over your search area. The estimate panel tells you how many images will be scanned and what it'll cost (if anything) before you commit.

**Real-Time Progress**

> Watch the scan run. Progress bar, phase indicator (coarse/fine), images fetched, matches found. Results appear as color-coded pins on the map as they're discovered. Green for strong matches, amber for moderate, red for weak.

**Two-Pass Scanning**

> Coarse grid covers the whole area at wider spacing. Anything that scores near your threshold triggers a fine-grid sweep of the surrounding area at tighter intervals and more heading angles. This is how you go from "somewhere in this neighborhood" to "this specific intersection facing northwest."

**Configurable Everything**

> Threshold sensitivity, grid spacing (coarse and fine), heading angles, rate limits, max results. Tune it for your use case. Searching a whole city? Widen the coarse grid. Narrowing down a single block? Crank the fine spacing down to 10 meters.

**CSV Export**

> Export all results with lat/lng, scores per algorithm, heading, phase, and direct links to view each match on its respective platform. Take it into a spreadsheet, a GIS tool, a report, whatever.

**Cost Estimation**

> Before you scan, GeoScout calculates the estimated image count and cost for paid providers. For Google SV, it shows what percentage falls under the free tier. No surprises.

**Fully Local**

> Runs on localhost. All Leaflet map libraries are bundled in the static folder; no CDN calls, no external dependencies at runtime. Your reference images stay on your machine. The only outbound requests are to the imagery APIs you choose to use.

---

## 🚀 Running It

### Don't want to install anything?

GeoScout is live as a Tor hidden service. Open [Tor Browser](https://www.torproject.org/download/) and go to:

```
rbu3z2ag3w4q7el6gnssyvwdv2ig3vuo2q7rldrkkm3th2qftfu76kad.onion
```

Same tool, same UI, no install. You just need Tor Browser.

---

### Option A: Download the .exe

Grab the latest zip from [**Releases**](../../releases), extract it, and double-click `GeoScout.exe`. It'll open your browser automatically. No Python, no install, no setup. That's it.

**Note:** The exe runs a local web server on your machine. It opens on port 5001, and you interact with it through your browser. It's not a native GUI app; it's a web app that happens to live on your machine. Nothing leaves your computer except the API calls you choose to make.

### Option B: Run from Source

If you want the raw files, the full code is right here in this repo.

1. Install Python 3.10+ from [python.org](https://python.org)
   * **CHECK "Add Python to PATH"** during install or I swear to god
2. Install dependencies:
   ```
   pip install flask pillow numpy
   ```
3. Clone or download this repo
4. Run it:
   ```
   python app.py
   ```
5. Open `http://localhost:5001` in your browser
6. That's it. It's running.

### Option C: Build your own .exe

If you want to build the executable yourself instead of trusting mine (respect):

1. Do Option B first to make sure it works
2. Double-click `build.bat`, or run it manually:
   ```
   pip install pyinstaller
   pyinstaller --onedir --noconsole --name GeoScout app.py
   ```
3. Copy `templates/` and `static/` into the `dist/GeoScout/` folder
4. Your exe is in `dist/GeoScout/`. Zip that whole folder if you want to share it.

---

## 🔑 API Keys

You bring your own. GeoScout doesn't ship with any keys, doesn't store them anywhere persistent, and doesn't send them anywhere except the provider you selected. They live in your browser session and nowhere else.

**Mapillary (Free)**

> Sign up at [mapillary.com/dashboard/developers](https://www.mapillary.com/dashboard/developers). Create a token as it costs nothing and has unlimited use.

**Google Street View (Paid with Free Tier)**

> Get a key at [console.cloud.google.com](https://console.cloud.google.com/apis/credentials). Enable the Street View Static API. You get $200/month free, which is roughly 28,000 image fetches.

**Baidu (Free Tier)**

> Create a developer account at [lbs.baidu.com](https://lbs.baidu.com/apiconsole/key). Get an `ak` key. Free tier is available; success in obtaining the key is not guaranteed.

**Satellite**

> ESRI and Sentinel-2 sources are free, no key needed. Google Satellite uses the same API key as Google Street View.

---

## 📁 Project Structure

| File | What It Does |
| --- | --- |
| `app.py` | The whole backend. All four search engines, comparison algorithms, coordinate conversion, scan management, and API routes. 499 lines on v1.0. |
| `templates/index.html` | The whole frontend. Leaflet map, provider panels, progress tracking, and result rendering. 404 lines. |
| `requirements.txt` | flask, pillow, numpy is the whole dependency list. |
| `build.bat` | One-click exe builder. Double-click it and walk away. |
| `static/` | Leaflet JS/CSS, Leaflet.Draw plugin, marker icons. All bundled locally. |
| `uploads/` | Where reference images go temporarily during a scan. Created automatically. |
| `results/` | Output directory. Created automatically. |

Two files. That's the entire application. Backend and frontend. I'm not sorry.

---

## ⚠️ Disclaimers

**API Keys & Charges**

> Your API keys never leave your machine except to hit the provider you chose. I am not responsible for your Google bill. The cost estimator is approximate. Check your provider dashboard for actual billing.

**No Warranty**

> This software is provided as-is, and it works for me. If it doesn't work for you, open an issue with what happened, and I'll look at it. If you just say "it's broken," I will stare at the wall and contemplate how I got here in life.

**Use Responsibly**

> This is an investigative tool that I built because I needed it for real work. Use it for journalism, research, OSINT, verification, and accountability. Please don't be a creep, I've investigated enough of those.

**Privacy**

> GeoScout has zero telemetry, zero analytics, zero tracking. Your uploaded images are stored locally in the `uploads/` folder during a scan, and that's it. Nothing phones home.

---

## 🐾 FAQ

**"What do I actually need to run this?"**

> Python 3.10+, three pip packages, and a browser. That's it.

**"Which mode should I use?"**

> Start with Mapillary since it's free and global. If you need better image quality or coverage in a specific area, try Google SV. If you're searching in China, use Baidu. If you're working from overhead/aerial imagery, use Satellite.

**"What if I get too many false positives?"**

> Raise the threshold. 55% is the default; bump it to 65-70% if you're getting noise. You can also disable comparison methods that aren't useful for your reference image. Color histogram is great for outdoor scenes but useless if your image is mostly grey concrete.

**"Can I search the whole planet?"**

> Technically yes, but please don't. The coarse grid generates sample points based on your search area size. Drawing a rectangle over all of Europe will create hundreds of thousands of points and either take forever or hit API rate limits. Keep your search areas reasonable. City-scale to neighborhood-scale works best.

**"Something is broken"**

> Open an issue and tell me what you did, what happened, and what you expected. Screenshots help, logs from the terminal help more, and vague complaints help no one and you will be fed to The Cat.

---

## 💬 Links

🌐 **Website:** [ephemeradev.net](https://ephemeradev.net)

💜 **Support development:** [ephemeradev.net](https://ephemeradev.net); tips welcome, never required

---

## Credits

**Created by Eph at Ephemera**

Built with UI assistance from Claude (Anthropic). Some features adapted from freely licensed code by various contributors who published their work for others to use and build on, the rest is my original work.

Leaflet and Leaflet.Draw are open source libraries by their respective authors. Imagery data comes from Mapillary, Google, Baidu, ESRI, and Sentinel-2/EOX.

---

## License

Custom license. Run it locally, modify it, and share the code; don't host your own public instance. See [LICENSE](LICENSE) for the full text.

---

*"She believed she'd lost the source code forever and then she found the files."*

🐾
