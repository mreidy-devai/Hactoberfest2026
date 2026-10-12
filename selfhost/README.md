# Self-hosted foot routing (OSRM)

By default the page asks the public OpenStreetMap foot router for loops, so your start point goes to that server. This folder runs the same open-source router (OSRM) on your own machine instead.

## Setup (once)

```bash
cd selfhost
./setup.sh            # New Jersey extract; takes a few minutes
# other regions: ./setup.sh https://download.geofabrik.de/<region>-latest.osm.pbf
```

Needs Docker. A state-sized extract needs roughly 1-2 GB of disk; a whole country is much bigger, so pick the smallest region that covers where you run.

## Run

```bash
./start.sh            # http://localhost:5000
```

Then open the page, expand **Settings**, and set **Routing server** to `http://localhost:5000`.

Check it works before blaming the page:

```bash
curl -i "http://localhost:5000/route/v1/foot/-74.1585,40.8173;-74.1700,40.7900?overview=false"
```

You should get `"code":"Ok"` and an `Access-Control-Allow-Origin` header (the browser needs it).

## From your phone

```bash
BIND=0.0.0.0 ./start.sh
```

On the phone, set Routing server to `http://<laptop-ip>:5000`, with the page also served over plain `http://` from the laptop. Anyone on that Wi-Fi can reach the server while it is bound to 0.0.0.0, so use it on a network you trust.

## What this does and does not change

- Loop generation (the part that needs your exact start point) stays on your machine.
- If your server is down or the extract does not cover the start point, the page shows an error. It does **not** fall back to the public router, on purpose.
- Still public: Nominatim geocoding of the start place name, Overpass tree/park data around the start, and Open-Meteo elevation for the generated routes. These are next to self-host (Nominatim and Overpass both have Docker images); typing coordinates or using "Use my location" avoids geocoding.
- Browsers may refuse an HTTPS page calling `http://localhost` or a LAN IP (mixed content). Serve the page over `http://` as in the main README.

## Not tested here

These scripts follow the OSRM project's documented Docker workflow, but I could not run Docker in the environment where I wrote them. If a step fails, the OSRM docs (`github.com/Project-OSRM/osrm-backend/wiki/Running-OSRM`) are the reference.
