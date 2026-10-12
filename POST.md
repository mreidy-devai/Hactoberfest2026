# Run Club Route Builder: say the run you want, get three loops

Type "flat 5K with trees, starts at Branch Brook Park" and get three loop options on a map, ranked by tree cover, water and hills, each with a GPX file for your watch. One static page, no build step, no API keys.

## Why open mattered

- **The model is swappable and free.** A 3B open-weight model (`qwen2.5:3b` through Ollama) turns the sentence into structured preferences on my own machine. Changing models is one text field, there's no per-call cost, and the request text never goes to an AI vendor.
- **The data is fixable.** Routing, parks, trees and water come from OpenStreetMap; elevation comes from Open-Meteo's open terrain data. When a route misses a footpath, the fix is an OSM edit, and it improves every route for everyone.
- **Every piece can be self-hosted.** Routing already can be: a setting points the page at an OSRM server running on my own machine, so loop generation stays local. The caveat: geocoding, tree data and elevation still use public OpenStreetMap and Open-Meteo services, so the start point still leaves the device and the page needs a connection. Those are the next pieces to bring home.

**Where open beat closed:** [FILL IN after the field test: something specific, e.g. a model swap, a fallback that kicked in, an OSM gap I could see and fix. Delete this line if nothing earned it.]

## What it gets wrong

"Foliage" is tree and park coverage from OpenStreetMap, not leaf color. Elevation comes from a coarse ~90 m grid, so short steep pitches disappear. [FILL IN: what the scorer got wrong on the actual run.]

## I took it outside

[FILL IN: date, route, distance. Did the shade and hills match what the page promised? Dead ends, road crossings, wrong trees? One photo.]

Code: [LINK]
