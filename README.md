# GeoScout

Street-view, satellite, and camera recon for figuring out where the hell an image came from.

*by Eph at [ephemeradev.net](https://ephemeradev.net) · est. 2026 · Open Source 💜*

Repo: [ephemera02/GeoScout](https://github.com/ephemera02/GeoScout)

GeoScout is for when you have a photo, a screenshot, a frame from a video, or some cursed little image fragment and you need to stop guessing. Upload the reference, draw a search box, choose your surface, and let the app sweep for visual matches instead of manually dragging around maps until your soul leaves your body.

It runs as a browser app, works locally, can be self-hosted on clearnet, and now has a proper command-center UI instead of looking like a generic chatbot sidebar somebody shipped at 3 a.m. and called a day.

## What She Does

- Uploads a reference image and strips metadata on ingest
- Searches a drawn area across multiple imagery sources
- Scores candidate images with multiple comparison methods
- Drops likely matches straight onto the map
- Pulls in public camera-source data
- Exports session data and CSV results when you want receipts

## How It Works

1. Upload your reference image.
2. Search a place or draw a target area on the map.
3. Pick the imagery surface you want to sweep.
4. Let GeoScout run the grid and start dropping matches.

Under the hood it combines:

- `pHash`
- `SSIM`
- `Color histogram`
- `Template matching`

You can adjust weights, threshold, spacing, headings, and provider settings depending on how chaotic your source image is.

## Search Modes

### Mapillary

Free, global, and usually the first place to start. If you want street-level imagery without instantly thinking about billing, this is your girl.

### Google Street View

Sharper imagery, more control, more enterprise energy, and yes, potentially billable. Good when you want stronger street coverage and finer heading control and are willing to let Google put a price tag on your curiosity.

### Baidu Panorama

For China coverage, because pretending Google solves every geography problem is loser behavior. GeoScout handles the coordinate conversion mess for you so you do not have to manually babysit that bullshit.

### Satellite

For overhead imagery, rooftops, compounds, lots, weird aerial references, and all the times street-level coverage is useless or flat-out gives you nothing.

## Camera Intelligence

GeoScout can also layer in camera data from:

- `OSM`
- `Shodan`
- `Insecam`

If a feed URL is reachable, you can also compare a camera frame against your uploaded reference.

## Current Tooling

- Draw-to-search workflow
- Live scan progress
- Match markers with source links
- Metadata-stripped uploads
- Clean reference download
- Session import/export
- Match CSV export
- Cost estimation before scanning
- Map layout controls for hiding the command center and overlays

## Running It

### Local

Install dependencies:

```bash
pip install -r requirements.txt
```

Run:

```bash
python app.py
```

Open:

```text
http://localhost:5001
```

### Public Deployment

Current HTTPS deployment:

[https://geoscout.ephemeradev.net/](https://geoscout.ephemeradev.net/)


## Keys

GeoScout does not ship with provider keys. Bring your own.

- `Mapillary` token for Mapillary scans
- `Google` API key for Google Street View and Google satellite usage
- `Baidu` key for Baidu panorama usage
- `Shodan` key for Shodan camera searches

The backend now rejects keyed scan modes if you try to start them with no key, because fake confidence is ugly.

## Project Shape

- `app.py` - backend routes, scan engines, comparison logic, camera-source logic
- `index.html` - main frontend template
- `requirements.txt` - Python dependencies
- `static/` - bundled frontend assets
- `uploads/` - local uploaded references
- `results/` - generated outputs

## Notes

- Uploaded references are stored locally.
- External providers can still be flaky because third-party services love ruining everyone's day.
- Paid providers may charge you depending on usage.
- This is an investigative tool, not a toy, even if the UI now has better hair.
- If something breaks, it is probably either an API, a rate limit, or some external service deciding to be deeply annoying.

## Intended Use

OSINT, verification, journalism, research, location analysis, and generally being more correct than the person loudly guessing in the group chat.

Use it legally. Use it responsibly. Do not use it to be a creep.

## Credits

Created by Eph / Ephemera.

Built with Flask, Pillow, NumPy, Leaflet, and Leaflet.Draw. Imagery and camera data come from external providers including Mapillary, Google, Baidu, ESRI, Sentinel-2, OpenStreetMap, Shodan, and Insecam.

Main site:

[https://ephemeradev.net/](https://ephemeradev.net/)

## License

See [LICENSE](LICENSE).

If the source image is being a little bastard, raise the threshold, tighten the search area, and change surfaces before you assume the whole app is lying to you.