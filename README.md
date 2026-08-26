# The Wausau Grower

A year-round garden companion for **Wausau Pilot & Review** readers — what to plant, when to plant it, and how to keep it alive in central Wisconsin's zone 4b/5a. Fully branded to the newsroom: the typewriter seal, the Oswald/Merriweather newspaper typography, and the teal pulled straight from the typewriter itself.

![The Wausau Grower planting calendar](docs/screenshot.png)

## What it does

**Planting Calendar** — a visual March–October timeline for 31 flowers, vegetables, and herbs, anchored to Wausau's ~May 15 average last frost and ~Oct 1 first frost, with a live "today" marker, fall succession windows, chart/list views, and a one-page printable version.

**Plant Guides** — searchable, filterable growing guides with sun, spacing, watering, difficulty, and zone-4b/5a-specific tips for every plant — each with its own hand-drawn SVG illustration (root vegetables get a below-ground cutaway), and a personal notes field per plant.

**Weather & This Week** — the live National Weather Service forecast for Wausau translated into garden decisions: frost-risk flags on nights ≤36°F, a "clear to transplant" status banner, rain probability, season-progress stats, a monthly task list, and a "planting windows open right now" panel generated from the plant database.

**Community Garden** — reader photo posts with likes, comments, a submission form, and a Garden of the Week badge.

**Built to bring readers back** — starred "My plants" lists, per-plant notes, and likes persist on the reader's device (localStorage, no account needed); every tab and plant guide has a bookmarkable deep link (`#weather`, `#plant/tomato`); a "Welcome back" strip summarizes which planting windows opened since the last visit and warns when a starred plant's window is about to close; and Bookmark/Share buttons plus a newsletter call-to-action close the loop with the newsroom.

## Running it

It's one self-contained HTML file. Open `index.html` in a browser, or serve it from any static host — no build step, no dependencies, no server-side code. External calls from the reader's browser: `api.weather.gov` (free, keyless, CORS-enabled) and Google Fonts (Oswald/Merriweather, with system serif fallbacks if offline). The Pilot & Review seal is embedded in the file itself.

## Configuration

A single `CONFIG` object at the top of the second `<script>` block:

- `sponsor` — set `{ name, url }` to show a "Presented by" masthead credit (renders with `rel="sponsored"`). `null` hides the slot.
- `submitEndpoint` — a URL accepting POST JSON `{who, where, kind, cap}` to route community submissions into a moderation queue. `null` runs the form in local demo mode.
- `newsletterUrl` — where the "Join the newsletter" buttons point. Defaults to the homepage; swap in a dedicated signup URL.

See [docs/deploy.md](docs/deploy.md) for the full production checklist and [docs/pitch.md](docs/pitch.md) for the concept proposal.

## Data sources

USDA 2023 Plant Hardiness Zone Map (Wausau sits on the 4b/5a boundary), regional climate normals for frost dates, and the National Weather Service API for live forecasts. Growing guidance is written for zone 4b/5a; a review pass by UW-Extension Marathon County is recommended before public launch.

## License

MIT — see [LICENSE](LICENSE).
