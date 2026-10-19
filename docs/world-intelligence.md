# World incidents (World screen, God's Eye View, Hermes tools)

The World screen shows incidents from open public feeds on a 3D globe, with a
list, filters, a detail card with the original sources, and the same
selection on the globe and in the list. Hermes can list, explain and show
incidents by voice or chat through three JARVIS MCP tools. Everything is free
and keyless. Nothing here runs unless the World screen is open or Hermes asks.

## What feeds it

| Source | What it gives | Evidence class | Severity | Refresh | Terms |
| --- | --- | --- | --- | --- | --- |
| USGS `4.5_week` GeoJSON | Earthquakes M4.5+ in the last 7 days | measured | from magnitude (and USGS PAGER alert) | 5 min | Public domain, credit USGS |
| GDACS `EVENTS4APP` | Earthquake, cyclone, flood, volcano, wildfire, drought alerts | agency (automated impact model) | from the GDACS alert level | 15 min | GDACS terms of use, credit GDACS |
| NASA EONET v3 (open, 30 days) | Curated natural events (storms, fires, volcanoes, ice, dust) | agency | from wind speed when given | 30 min | NASA open data, each event links its own source |
| NOAA NHC `CurrentStorms.json` | Active Atlantic and East Pacific storms | agency | from the storm class | 30 min | Public domain |
| GDELT 2.0 event exports | Protests and violence machine-coded from news | **media** (never confirmed) | never rated | 15 min, rolling 24 h | Free with citation of the GDELT Project |

Not used: World Monitor (AGPL-3.0 code, and its API terms forbid use inside
another product), ACLED (registration and non-commercial terms), Cloudflare
Radar, OpenSky and IODA (keys, accounts or terms that do not fit a free
product). No feed needs an account or a key.

## How the evidence is kept apart

Every incident carries one `evidence` value, shown as a badge everywhere:

- **measured**: an instrument network measured it (USGS).
- **agency**: a public agency published it as an alert or curated event. GDACS
  alert levels come from an automated model, and the card says so.
- **media**: software read news articles and coded them (GDELT). The card,
  the list and the Hermes tools call it an unconfirmed media report every
  time. A media report is never shown or spoken as a confirmed event.

Severity (0 to 4) is set only from a scale the source gave (magnitude, alert
level, wind speed, storm class); otherwise it is "not rated". A position the
source did not give, or gave only as a country centroid, is not invented: the
incident stays in the list without a place on the globe. Reports of the same
event from different feeds (a USGS quake and the GDACS alert for it, the
GDACS, EONET and NHC records of one storm) are linked as "other reports of the
same event", never merged, so their disagreements stay visible.

## How it fits together

```
acquisition       jarvis/intel/service.py   fetch on demand, per-source interval,
                                            lock, doubling backoff, size caps
normalization     jarvis/intel/parsers.py   one IntelEvent per source record,
                  jarvis/intel/model.py     guards for text, URLs, positions
store             jarvis/intel/store.py     replace-by-source, revision, links, cap 3000
query             jarvis/intel/query.py     time, kind, category, region, severity, evidence
HTTP              /api/intel/events?rev=, /api/intel/events/{id}, /api/intel/sources
orchestration     jarvis/intel/mcp_tools.py world_incidents, world_incident, world_globe
rendering         World screen (React) and God's Eye View (Cesium) through postMessage
```

There is no background poller and no second agent loop. The World screen asks
`/api/intel/events` once a minute while it is open, sending the revision it
has; an unchanged store answers in a few bytes. Hermes is the only agent: its
tools read the same store, and `world_globe` only changes what the World screen
shows (it publishes `NavigateSidebar` and `WorldIntelCommand` on the event bus).

## Hermes tools

| Tool | Does | Example request |
| --- | --- | --- |
| `world_incidents` | Lists incidents, most severe first, with filters (hours, kinds, category, region, minimum severity, evidence) and each feed's status | "Show major global incidents from the last 24 hours" |
| `world_incident` | One incident with its evidence, what is measured or reported versus not rated, every source link, and related reports from other feeds | "Investigate this incident and summarize what's confirmed versus uncertain", "Compare reports from multiple outlets" |
| `world_globe` | `focus` an incident, `filter` the screen, or `reset` to the whole globe | "Filter the globe to South Asia and natural disasters", "Focus on this event and show me the original sources", "Return to the global view" |

Feed text reaches Hermes inside quotes under a fixed line saying it is data to
report, never an instruction. Hermes picks these tools up from the JARVIS MCP
server; the Hermes `config.yaml` `jarvis` include list needs the three names
(the free-voice setup in `jarvis/cli_ctl/free_voice.py` writes them; a
config written before this change needs them added).

## God's Eye View contract (`events@1`)

GEV announces `gev:ready { capabilities: ['view', 'events@1'] }`. JARVIS then
sends only drawing fields (id, lat, lon, kind, category, severity, evidence,
title, time, source), never links or summaries:

- `gev:events-upsert { v: 1, id, replace, events }`, answered by
  `gev:events-applied { id, ok, applied, removed, rejected, truncated }`
- `gev:events-remove { v, ids }`, `gev:events-clear`
- `gev:events-select { v, eventId | null, fly }`
- `gev:events-home { v, center? }` (the whole globe over `center`)

GEV reports `gev:event-selected { v, eventId, via: 'click' }` and
`gev:event-selection-cleared { v, reason }`. GEV accepts incident messages only
from the page that frames it (`location.ancestorOrigins`, or a loopback origin
when the browser does not give one) and answers only that origin. JARVIS
accepts GEV messages only from its own frame and origin, and a selected id only
if JARVIS sent it. GEV caps the list at 2000, titles at 160 characters, ids to
`[A-Za-z0-9:._-]{1,200}`, and drops anything malformed. Markers live in one
`CustomDataSource`, one entity per id, removed when the page drops them and
destroyed with the embed. A GEV without `events@1` gets no incident messages.

The GEV change is two commits on top of GEV `aa16b7c`
(`docs/world-intelligence/gev-incident-layer.patch`): a new
`src/layers/externalEvents/` module, the embed wiring in `src/app/embed.js`,
tests, and the two script manifests GEV checks. GEV keeps world text in its
overlay host, so the selected incident is ringed rather than labelled; its
title is in the JARVIS card.

## Security

- Feed text is plain text: tags, control characters and bidirectional
  overrides are removed, whitespace collapsed, length capped. It is rendered as
  text, never as markup.
- Only `http(s)` links without credentials survive; they open with
  `rel="noopener noreferrer"`. Anything else is shown as text.
- Positions must be finite and in range; `(0, 0)` counts as "no position".
- Downloads are capped (4 MB JSON, 3 MB zip, unzipped size checked) and time
  out; a failed feed backs off (60 s doubling to 30 min) and its incidents are
  marked stale, not removed.
- The store is capped at 3000 incidents, the globes at 800 markers (most severe
  first), GEV at 2000.
- No secret is involved, so none reaches the browser.

## Install and update

1. JARVIS: the code ships in the app (backend, frontend bundle, MCP tools).
   Nothing to configure.
2. God's Eye View, in its checkout:
   `git am docs/world-intelligence/gev-incident-layer.patch` (or `git apply`
   when other work is in progress, then commit only those paths). A running
   `npm run dev` picks it up; otherwise restart it.
3. Hermes: add `world_incidents`, `world_incident` and `world_globe` to the
   `jarvis` MCP include list in `config.yaml` if they are not there, then
   restart Hermes.

## Rollback

- JARVIS: revert the commits from "Add world incident service from open public
  feeds" to "Open the World screen on the whole globe" (and the bundle commit
  after them). The World screen goes back to the earlier news and quake view.
- GEV: `git revert` the two commits ("Draw incidents a framing page sends, with
  two-way selection" and "Let the framing page bring the whole globe into
  view"). JARVIS sees no `events@1` and sends nothing.
- Hermes: remove the three tool names from the include list.

## Limitations

- GDELT is noisy: an article about events elsewhere, or a fact-check, can be
  coded as an event in the wrong place. That is why it is always "media".
- GDACS alerts carry the alert level of the whole event and of the latest
  update; both are shown ("red alert (latest update green)").
- Linking is by distance and time (quakes within 100 km and 30 minutes) or by
  storm name and distance (600 km). A swarm of nearby quakes can link to
  several alerts.
- NHC covers the Atlantic and East Pacific only; other basins come from GDACS
  and EONET.
- The first GDELT refresh fetches 6 of the 96 files in the 24 h window; the
  window fills over the next refreshes.

## Licences and attribution

USGS and NOAA data are public domain (credit requested). GDACS data are used
under its terms of use with attribution. NASA EONET is open data; each event
links its original source. GDELT is free to use with citation of the GDELT
Project. God's Eye View is MIT; Cesium is Apache-2.0. The World screen footer
and every detail card carry the source name, attribution and terms link.
