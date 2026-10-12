# Run Club Route Builder

Type what you want ("5K, shaded, fall foliage, starts at Branch Brook Park") and get three loop options on a map, ranked by tree and park cover. Download any route as GPX for your watch or phone.

Built for the Hacktoberfest "Touch Grass" challenge. The screen should be the shortest part of the run.

## How it works

1. **Parse the request** with a small open-weight model running locally through [Ollama](https://ollama.com) (default `qwen2.5:3b`). If Ollama isn't reachable, a built-in regex parser takes over, and the page tells you which one ran.
2. **Find the start** with OpenStreetMap Nominatim, or use your phone's location.
3. **Generate loops**: six candidate loops in different directions, routed on real paths and sidewalks by the OSM foot router.
4. **Score them** with OpenStreetMap data from Overpass: how much of each route is inside parks, woods, and grass, or within about 20 m of a mapped tree, plus nearness to water.
5. **Check hills** with [Open-Meteo](https://open-meteo.com/en/docs/elevation-api) elevation (Copernicus DEM): up to 100 points per route, total climb with small bumps filtered out, and a mini elevation profile on each card.
6. **Rank** by your preferences (shade, foliage, green, water, flat, hilly) minus a penalty for missing your target distance. "Flat" and "hilly" only affect ranking when you ask for them; climb is always shown.

No framework, no build step, no API keys.

## Run it

```bash
cd run-club-routes
python3 -m http.server 8000
# open http://localhost:8000
```

### Local model (optional but the point)

```bash
ollama pull qwen2.5:3b
OLLAMA_ORIGINS="*" ollama serve
```

`OLLAMA_ORIGINS` lets the browser page call Ollama. Settings on the page let you change the URL and model.

### On your phone

- Serve the page from your laptop and open `http://<laptop-ip>:8000` on the same Wi-Fi.
- To use the laptop's model from the phone, start Ollama with `OLLAMA_HOST=0.0.0.0` and set the Ollama URL in Settings to `http://<laptop-ip>:11434`.
- Or skip the model: the fallback parser handles typical requests.
- The geolocation button needs HTTPS on most phones; `localhost` is exempt, a LAN IP is not. Typing a start place works everywhere.

## Honest limits

- "Foliage" is a **proxy**: tree and park coverage in OpenStreetMap, not actual leaf color. Mapped trees are sparse in some areas, so suburban street shade may be undercounted.
- Elevation comes from a coarse (~90 m) terrain grid. Big hills show up; short steep pitches and bridges/underpasses do not, and small bumps under 3 m are ignored on purpose. Treat climb as a rough guide.
- Routes depend on public OSM services (rate limited; fine for personal use, not for a deployed public app).
- The map tiles, geocoding, tree data and elevation need internet. Routing can be self-hosted (see above). A fully offline version also needs cached tiles and local copies of the other three.
- The self-hosting scripts are untested: I could not run Docker where I wrote them.

## Privacy, precisely

Your request text is parsed by the local model and never goes to an AI vendor. The **start location** does go to public services: OpenStreetMap geocoding and Overpass, Open-Meteo for elevation, and the public OSM router.

**Self-hosted routing:** `selfhost/` runs OSRM in Docker on your own machine; put its address in Settings > Routing server and loop generation stays local. See `selfhost/README.md`. Geocoding, tree data and elevation are still public, so this is a step toward private and offline, not the whole way.

The write-up for the challenge is in `POST.md`.

## Field test checklist (bonus points)

- Run or walk one generated route and note how it matched reality (shade? foliage? dead ends? road crossings?).
- Note anything the scorer got wrong; that is the most honest part of the write-up.
- Photo of the route on the trail.
