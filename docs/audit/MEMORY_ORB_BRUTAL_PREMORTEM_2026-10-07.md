# Memory Orb: Brutal Pre-Mortem (2026-10-07)

Status: **NOT SHIP-READY.** Static code audit, no code changed.

Scope: the Memory Orb section (`nav id "orb"`) and the backend data it reads. This pass does not cover the rest of the UI that the spec's §21 asks for. Where another surface duplicates the Orb, it is listed in §F only.

Method: I read the code and the existing live screenshots in `jarvis-handoff/live-test/` (`v2/1-memory-orb.png`, `v2/3-orb-search.png`, `import/reimport_ui.png`, `import/reimport_ui_search.png`). I did not run the app, and I took no performance measurements.

Evidence convention: every code claim carries a `file:line`. Paths are shortened as follows.

| Short | Path |
|---|---|
| `View` | `jarvis/ui/web/frontend/src/views/MemoryOrbView.tsx` |
| `Orb` | `jarvis/ui/web/frontend/src/lib/orbGraph.ts` |
| `OrbTest` | `jarvis/ui/web/frontend/src/lib/orbGraph.test.ts` |
| `Import` | `jarvis/ui/web/frontend/src/views/MemoryOrbImport.tsx` |
| `Wiki` | `jarvis/ui/web/wiki_routes.py` |
| `Page` | `jarvis/memory/wiki/page.py` |
| `Server` | `jarvis/ui/web/server.py` |
| `Tools` | `jarvis/ui/web/tools_routes.py` |
| `Skills` | `jarvis/ui/web/skills_routes.py` |
| `Mcp` | `jarvis/ui/web/mcp_routes.py` |
| `Market` | `jarvis/ui/web/marketplace_routes.py` |
| `Store` | `jarvis/ui/web/frontend/src/store/events.ts` |
| `Nav` | `jarvis/ui/web/frontend/src/components/layout/navGroups.ts` |
| `WG` | `jarvis/ui/web/frontend/src/lib/wikiGraph.ts` |
| `WGView` | `jarvis/ui/web/frontend/src/components/wiki/WikiGraph.tsx` |
| `Cam` / `Cont` / `Forces` | `lib/graphCamera.ts` / `lib/graphContinuity.ts` / `lib/graphForces.ts` (frontend) |

A statement marked **(inferred)** is reasoned from the code but was not observed at runtime.

---

## 0. Verdict in one paragraph

The Orb is a **star diagram of the app's catalogs**, not a memory map. Five unrelated REST lists (`View:57-72`) are hung off invented hub nodes (`Orb:104-105`, `Orb:112`, `Orb:141-145`, `Orb:164-168`, `Orb:187-191`, `Orb:211`) and drawn with a default force layout. Apart from wiki links between notes, almost every edge on screen is fabricated: "child of hub" is drawn with the same style as a real wikilink (`View:216-217`). The headline count includes those hub nodes (`View:241`). Search dims the graph but never moves the camera, and a search with no hits looks identical to no search (`View:165`). Filters do not update counts (`Orb:240`), and hiding Notes orphans the Concepts (`Orb:112`, `Orb:236-239`). Fit frames every node, outliers included (`View:262`), which the codebase's own `Cam:5-9` documents as broken. "Online" means only that the WebSocket is connected (`View:193` + `Server:1118-1123`), and "UNKNOWN" is a backend fallback string drawn as if it were a model name (`Server:1126`, `View:307-310`). The reactor shows voice state only and is `aria-hidden` (`View:300`, `ReactorCore.tsx:35`). The data model has to be fixed first. Any visual work done before that will polish fabricated structure.

---

## 1. What the Orb actually shows (data-model answers to spec §8)

| Question | Answer | Evidence |
|---|---|---|
| Where do the "2,553 objects" come from? | `full.nodes.length - 1`, which counts every node except `core`, **including the synthetic hub and category nodes**. With the spec's counts, 2357+1+30+91+47+3 = 2529 real items. The remaining **24 are invented hub nodes**. The screenshots match: 203 shown against 180 real (`import/reimport_ui_search.png`), and 197 against 174 (`v2/1-memory-orb.png`). | `View:241`, `Orb:104-105` |
| How are relationships generated? | (a) Real: wikilinks between pages (`Wiki:613-636`, filtered `Orb:132-136`). (b) Fabricated: every note is linked to `hub:wiki` (`Orb:129`), every skill to a category hub (`Orb:144-157`), every tool to a risk-tier hub (`Orb:166-180`), every app to a category hub (`Orb:189-204`), every MCP server to `hub:mcp` (`Orb:225`), and every hub to `core` (`Orb:105`). | as cited |
| Are the relationships real? | Only the wiki-to-wiki links are. **No edge connects memory to any capability**, and none connects a capability to another capability. An MCP tool is not linked to its MCP server, although the API sends `mcp_server` (`Tools:30-34`), and an app is not linked to the tools it provides. | `Orb:47-50` (fields dropped), `Tools:30-34` |
| Do duplicates exist? | Yes. (1) The backend drops pages whose filename stem repeats, without saying so: `slug = path.stem` (`Page:106`) is deduplicated in `Wiki:598-602`. Imports keep original filenames under `imports/<source>/` (`importer.py:5`, `importer.py:60`), so README/index/Untitled collisions are likely **(inferred)**. (2) Edges from a dropped page are still emitted under the surviving page's slug, because the edge loop iterates over every page, not the deduplicated set (`Wiki:613-627`). (3) Repeated links are not deduplicated (`Wiki:615-627`, `Orb:132-136`). (4) Counts are incremented **before** `add()` deduplicates by id (`Orb:116` vs `Orb:98-100`; also `Orb:146`, `Orb:169`, `Orb:192`, `Orb:213`), so a repeated name inflates the count without adding a node. (5) One integration can appear three times, as an app, a tool and a `plugin-*` skill: `v2/3-orb-search.png` shows `google_calendar`, `plugin-google_calendar` and "Google Calendar" as separate, unlinked nodes. The `plugin-<id>` sibling skill pattern is at `brain/manager.py:6272`. | as cited |
| Are there stale objects? | Apps are the **whole marketplace catalog**, not the user's accounts. Every catalog plugin is emitted (`Market:373-427`), yet the hub calls them "Accounts the assistant can use" (`Orb:187`). | `Market:373`, `Orb:187` |
| Do categories overlap? | Yes. "Tools" is the brain's effective registry, which already contains MCP adapters and Marketplace/CLI tools (`Tools:3-4`, `Tools:40-44`, `Tools:17-21`). The same capability is therefore counted under Tools and again under MCP servers or Apps. | as cited |
| Are counts authoritative? | No. They are client-side tallies of whatever arrived. A catalog that fails to load is counted as **0** (`View:46-55`, comment `View:51-53`). During boot the Tools count is the MCP-only fallback registry (`Tools:45-49`), and MCP shows "not-initialized" servers as Stopped (`Mcp:170-193`, `Orb:214`). Invalid and disabled skills are counted as Skills (`Orb:146` vs `Orb:153`). | as cited |
| Memory, capabilities, or history? | All three are mixed together. Wiki pages include session rollups (`Page:31-38`: `sessions` maps to `session`) and imported chat exports (`Import:150`). Neither is a "memory" in the user's sense. | as cited |
| Why "1 Concept"? | `CONCEPT_KINDS = {concept, project, entity}` (`Orb:75`). Page kind is frontmatter `type` or the folder (`Page:111-113`), otherwise `"meta"` (`Wiki:204-206`). Imported notes have neither, so roughly 93% of the graph (2357/2529) is an undifferentiated green "Notes" mass. The 1 is truthful, but it shows that **the data is unclassified**, not that the user has one concept. | as cited |

**Should memory and capabilities share one graph?** No. They have different semantics: memory is authored and linked, while capabilities are a runtime inventory with status. The only things connecting them are invented hubs. Section I proposes splitting them.

---

## A. Top 20 visual failures

| # | Failure | Evidence | P |
|---|---|---|---|
| A1 | Notes make up about 93% of nodes and share one green (`#5bd4a4`), so the screen is "too much green, no information". | `Orb:55`, `Orb:115` | P1 |
| A2 | Every node is a filled circle. Group is encoded **by colour alone**: no shape, icon or glyph. | `View:170-173`, legend dots `View:287-290` | P1 |
| A3 | Node size encodes **graph role** (core 10, family 7, hub 4, leaf 3), not importance, recency or link count. A 300-backlink note is drawn the same size as an orphan. `WG:210-212` already has `nodeRadius(backlinkCount)` but it is unused here. | `View:40-44` | P1 |
| A4 | Every edge has the same colour (`rgba(120,200,255,0.12)`) and width (0.6). Fabricated hub edges look exactly like real wikilinks. | `View:216-217` | P0 |
| A5 | Leaf labels appear only above zoom 2.6. At any zoom that fits 2.5k nodes, the map has no labels **(inferred)**. | `View:176-181` | P1 |
| A6 | Label sizes in screen pixels are 13/10/8.5 at zoom ≥ 0.6 and shrink proportionally below that. 8.5px leaf labels are below a readable minimum. | `View:183` | P1 |
| A7 | There is no label collision handling at all: no background, no culling, no offset. The overlap is visible in `v2/3-orb-search.png` ("Google Calendar"/"Calendar"). | `View:184-188` | P1 |
| A8 | There is no node collision force anywhere in the graph code. Nothing in `lib/` or `components/wiki/` uses `forceCollide`, so leaves at link distance 16 pile onto their hub. | `View:142-144` | P1 |
| A9 | Brightness has two meanings: dim means "not live" (0.35) **and** dim means "not a search hit" (0.12). These are visually indistinguishable at a glance. | `View:167` | P0 |
| A10 | Glow (`shadowBlur`) is applied to every live node, so it carries no meaning beyond "live", which is already encoded by alpha. Tools are always `live: true`. | `View:168-169`, `Orb:174` | P2 |
| A11 | The legend shows the same hex values that mean **different things** in the Notes map: `#6aa9ff` is an entity there but Apps here, `#b48cf2` is a concept there but Skills here, `#ffb84d` is a project there but Concepts here, and `#f47fa4` is a broken link there but Tools here. | `WG:89-102` vs `Orb:53-61` | P1 |
| A12 | The decorative 150px reactor permanently occludes the bottom-right of the graph area. | `View:298-301` | P1 |
| A13 | The Import dialog opens at the same position as the Filter panel and covers it (`Import:140` `absolute right-4 top-16` vs `View:273` `absolute right-4 top-16`). Confirmed in `import/reimport_ui.png`. | as cited | P1 |
| A14 | The layout is a starburst of fixed link distances (95/45/16). Clusters reflect **catalog grouping**, not semantics, and the empty space comes from hub spokes. | `View:142-144` | P1 |
| A15 | A full-screen radial-gradient wallpaper sits behind the canvas, which adds to the "debug viz in a dark room" feel. | `View:198` | P3 |
| A16 | Naming is inconsistent: the hub says "Memory" (`Orb:112`) while the filter says "Notes" (`Orb:64`), and the title is a monospaced uppercase "MEMORY ORB" (`View:237-239`). | as cited | P2 |
| A17 | The status pill is uppercase mono text at 10px (`View:302`), shown even when it reads "UNKNOWN". | `View:302-310` | P1 |
| A18 | MCP shows "0" with its full colour and filter row even when there are none (`v2/1-memory-orb.png`), which suggests data that is not there. | `View:276-294` | P2 |
| A19 | The ask-answer bubble has no max-height and no scroll, and renders raw text (`reply.content`). A long answer covers the graph **(inferred)**. | `View:345-350` | P2 |
| A20 | Strings are hardcoded English although the nav uses i18n keys (`Nav:130`). | `View:238`, `View:249`, `View:265`, `View:275` | P3 |

## B. Top 20 UX failures

| # | Failure | Evidence | P |
|---|---|---|---|
| B1 | **Search never moves the camera.** There is no `centerAt` or `zoomToFit` on query. It only changes alpha. `import/reimport_ui_search.png` shows the match as a 6px label in a corner. | `View:129`, `View:165` | P0 |
| B2 | **A search with no hits looks the same as no search**: `lit = matches.size === 0 \|\| …`. A typo shows the full bright graph, with no "0 results". | `View:165` | P0 |
| B3 | Search runs on `full`, not on the filtered graph. Hidden matches count toward `matches.size`, so visible results lose their labels once there are 40 or more hits, while every visible non-match is dimmed. The screen goes dark with nothing found. | `View:129`, `View:180` | P0 |
| B4 | Search also matches the `detail` field. Details are boilerplate (`"<Kind> page in the wiki"`, `"Connected · …"`, `"Not connected · …"`, `"Running"`/`"Stopped"`), so "page", "wiki", "meta" and "connected" match hundreds of nodes. | `Orb:249-250`, `Orb:125`, `Orb:201`, `Orb:222` | P1 |
| B5 | Search covers titles only and uses substring matching: no fuzzy match, no content search. The backend has FTS search (`Wiki:679-723`) that the Orb does not use. | `Orb:245-252` | P1 |
| B6 | There is no results list, no result count, no Enter-to-focus, no next/previous and no Escape-to-clear. | `View:244-258` | P1 |
| B7 | **"Open" does not open the item.** `setActiveSection(node.section)` lands on the section root. `node.slug` is stored (`Orb:128`) but never used, although `requestWikiPage(slug)` exists (`Store:731-736`) and is consumed by `WikiView.tsx:81-84`. Skills, tools, apps and MCP servers likewise land on a list page. | `View:327-334` | P0 |
| B8 | **Filters do not update counts.** `filterOrbGraph` returns `counts: graph.counts` unchanged, and the headline uses `full`. | `Orb:240`, `View:241`, `View:292` | P0 |
| B9 | **Hiding Notes orphans Concepts.** `hub:wiki` is in group `wiki` (`Orb:112`), so the filter removes it, and concept nodes lose their only parent edge (`Orb:236-239`) and drift. | as cited | P0 |
| B10 | "Nothing" filter state: only the `core` dot remains (`Orb:236` never hides core), with no empty-state message. | as cited | P1 |
| B11 | A filter's active/inactive state is shown only as `opacity-35`, with no checkmark and no "n hidden" summary. `aria-pressed` is present (good). | `View:280-285` | P2 |
| B12 | The detail panel shows only a label and one line. It has no relationships, no backlinks (`Wiki:647-676` exists), no source path, no last-modified date and no "why is this here". | `View:316-340` | P1 |
| B13 | Hover replaces the detail panel. Moving the mouse off a node closes it unless the node was clicked, and the hover and pick panels look identical. | `View:316`, `View:224` | P2 |
| B14 | Clicking any node, hubs included, always zooms to 3×. It does not frame the node's neighbourhood. | `View:225-229` | P1 |
| B15 | There is no keyboard path into the graph: the canvas has no focusable nodes, no arrow navigation and no Escape. | `View:201-231` | P1 |
| B16 | There are no zoom limits (no `minZoom`/`maxZoom`), so the user can zoom out to nothing. The Notes map sets 0.1/8 (`WGView:830-831`). | `View:201-231` | P2 |
| B17 | There is no loading skeleton. While loading the canvas is empty and the subtitle reads "Mapping…". `useQuery` errors are never read. | `View:108-112`, `View:241` | P1 |
| B18 | The ask bar is a second chat input. Its "reply" is the newest assistant message with `ts >= sentAt-1000`, so an unrelated voice or proactive message can be shown as the answer. It sends no graph context (no selected node). | `View:133-136`, `View:155-160` | P1 |
| B19 | The status contradicts the shell: the sidebar says "Voice unavailable" while the Orb says ONLINE (`import/reimport_ui.png`). The Orb ignores `voiceReady` (`Store:448`). | `View:193` | P0 |
| B20 | After a filter toggle or data refresh, the graph re-explodes and the camera stays where it was (`fitted` is one-shot). The user loses spatial context. | `View:99-100`, `View:122-128`, `View:219-223` | P1 |

## C. Top 20 architecture / data failures

| # | Failure | Evidence | P |
|---|---|---|---|
| C1 | Fabricated hub topology is presented as structure, and the tests lock it in (`OrbTest:29-37` asserts the hub edges). | `Orb:104-105`, `Orb:129` | P0 |
| C2 | The headline count includes about 24 synthetic hub nodes. | `View:241` | P0 |
| C3 | A failed catalog fetch is shown as **0** rather than as unavailable. The swallowing is deliberate (`View:51-53`). | `View:46-55` | P0 |
| C4 | `/api/wiki/graph` returns **HTTP 200** `{ok:false,error}` on failure (`Wiki:583-585`). `getJson` accepts it because `res.ok` (`View:49`), and `buildOrbGraph` then evaluates `wiki.nodes.length` on `undefined` (`Orb:111`). That throws a TypeError, and the whole view crashes into the error boundary **(inferred)**. | as cited | P0 |
| C5 | An unconfigured or missing vault returns `ok:true` with empty arrays (`Wiki:577-579`). The Orb silently drops the Memory family, with no "memory not configured" state. | as cited | P1 |
| C6 | Counts are incremented before deduplication, so duplicate ids inflate them. | `Orb:116`, `Orb:146`, `Orb:169`, `Orb:192`, `Orb:213` vs `Orb:98-100` | P1 |
| C7 | The backend drops duplicate-stem pages without saying so and misattributes their edges. | `Page:106`, `Wiki:598-602`, `Wiki:613-627` | P1 |
| C8 | Duplicate edges are not deduplicated, in the backend or the client. | `Wiki:615-627`, `Orb:132-136` | P2 |
| C9 | Page kind defaults to `"meta"`, so imported content has no type. The Concepts/Notes split is therefore meaningless for imported vaults. | `Wiki:204-206`, `Page:111-113`, `Orb:75` | P1 |
| C10 | The graph payload has no `mtime`/`size` per node, although the scan has them (`Wiki:219-225`). "What changed recently?" cannot be answered. | `Wiki:603-609` | P1 |
| C11 | Payload fields the Orb drops: `hub` (the user's pivot page, `Wiki:643`), `broken[]` and edge `context` (`Wiki:619`, `Wiki:640`). The Orb's source type does not even declare them. | `Orb:43-46` | P1 |
| C12 | Tools double-count MCP tools and Marketplace tools (`Tools:17-21`, `Tools:40-44`), and the Orb ignores `source`/`mcp_server`. | `Orb:48` | P1 |
| C13 | Apps means the whole catalog; `live_callable` (`Market:424`) and `needs_reauth` are ignored. The backend calls a connected app with no live tools a lie (`Market:316-325`), yet the Orb paints it bright. | `Orb:193` | P1 |
| C14 | The MCP `error` is ignored. A server that is "running" with an open circuit breaker draws bright (`Mcp:200`, `_spec_to_dict` error field). The boot state "not-initialized" is shown as "Stopped" (`Mcp:184`, `Orb:214`). | as cited | P1 |
| C15 | **Two capability sources of truth**: the Orb reads Jarvis registries (`View:58-64`), while the Tool Armory reads the **Hermes** inventory (`ArmoryHermes.tsx:7-11`, `:145`). That violates the spec rule "Hermes = agent authority". | as cited | P0 |
| C16 | **Two brain-status sources**: the Orb fetches `/api/brain/status` once (`View:113-117`, and no one invalidates `["brain","status"]`), while the store has live `brainProvider`/`brainModel` updated by WS events (`Store:528-532`, `Store:901-902`). The model pill goes stale after a provider switch **(inferred)**. | as cited | P1 |
| C17 | "Online" is meaningless: the server never returns an empty provider (falling back to `cfg.brain.primary` or the literal `"unknown"`, `Server:1118-1123`), so `online` is just `connected` (`View:193`). | as cited | P0 |
| C18 | "UNKNOWN" is the server's fallback when the active provider config has no `model` (`Server:1124-1126`). It is rendered verbatim as a model name (`View:307-310`). | as cited | P0 |
| C19 | The Orb does not reuse the repo's existing graph infrastructure: robust framing (`Cam:5-9`, `Cam:96`), position continuity (`Cont:4-13`), the centring force for strays (`Forces:5-11`), kind colours and backlink radius (`WG:89-96`, `WG:210-212`). It is a parallel, weaker graph stack. | as cited | P1 |
| C20 | The Orb's tests cover only the hub-building happy path. Nothing tests: failed or `ok:false` sources, filter count updates, search with hidden groups, or no-hit search. | `OrbTest:28-72` | P1 |

## D. Top 20 performance risks

None of these was measured; the app was not run. All are risks reasoned from the code.

| # | Risk | Evidence | P |
|---|---|---|---|
| D1 | Every filter toggle rebuilds fresh node copies **without positions**, so 2.5k nodes re-layout from scratch for 220 ticks. | `View:122-128`, `View:218` | P1 |
| D2 | Any data change rebuilds `full` and then `shown`, which forces a full relayout. React Query refetches on focus or remount after a 30s `staleTime`, and the assistant writes wiki pages regularly, so the graph re-explodes **(inferred)**. `Cont:4-13` exists to prevent exactly this. | `View:108-112`, `View:119` | P1 |
| D3 | `ctx.shadowBlur` is applied per live node per frame. Canvas shadows are expensive at 2.5k nodes **(inferred)**. | `View:168-169` | P2 |
| D4 | The view subscribes to `messages`, so every chat message update re-renders the whole Orb and recreates `drawNode`, the pointer painter and the link callbacks. Whether that happens per streaming token is **(inferred)**. | `View:105`, `View:162` | P2 |
| D5 | Hover is React state, so every hover re-renders the whole view and creates new canvas callbacks. | `View:95`, `View:224` | P2 |
| D6 | The Orb fetches full `/api/tools`, with every schema ("50-200k characters", `Tools:72-76`), just to read name and description. `/api/tools/brief` exists. | `View:61` | P2 |
| D7 | The same applies to `/api/skills` ("tens of kilobytes", `Skills:359-361`); `/api/skills/brief` exists. | `View:60` | P3 |
| D8 | `/api/wiki/graph` ships a context snippet (up to about 200 chars) for **every** edge, which the Orb then discards. | `Wiki:619-627` | P2 |
| D9 | Each `/graph` call re-walks and stats the vault. Parsing is cached (`Wiki:242-259`), but the walk is still O(files) per request. | `Wiki:337` | P3 |
| D10 | The search has no debounce: every keystroke scans 2.5k labels and details and re-renders the canvas. | `View:129`, `Orb:245-252` | P3 |
| D11 | The charge strength is fixed at -60 with no scaling for node count, so the many-body force covers all 2.5k nodes. | `View:141` | P2 |
| D12 | The fixed `cooldownTicks={220}` does not adapt to graph size. A large graph may stop unsettled and a small one wastes ticks **(inferred)**. | `View:218` | P3 |
| D13 | There is no level-of-detail rendering: every node is drawn at every zoom level, and only labels are gated. | `View:162-191` | P2 |
| D14 | Pointer hit areas are painted for every node on the shadow canvas, which doubles draw work. | `View:210-215` | P3 |
| D15 | The import poll runs every 800ms for the lifetime of a job and invalidates the whole `["orb","sources"]` query at the end, so every catalog is refetched to pick up new notes. | `Import:76-88`, `Import:83` | P3 |
| D16 | The reactor's animated SVG (`deck-reactor-coils`, `index.css:2029-2036`) composites over a live canvas. | `View:300` | P3 |
| D17 | `useSize` starts at 800×600, so the first frame renders at the wrong size and then resizes **(inferred)**. | `View:75` | P3 |
| D18 | Five parallel fetches share one loading flag, so the slowest catalog gates the whole map. | `View:57-72` | P2 |
| D19 | There is no virtualization or clustering: the full graph is handed to the renderer at once. | `View:203` | P2 |
| D20 | There is no instrumentation (render or layout timings), so performance cannot be verified or regression-tested. | absent in `View` | P2 |

## E. Top 20 "looks impressive but is actually useless"

| # | Feature | Why it is useless | Evidence | P |
|---|---|---|---|---|
| E1 | The central "Jarvis" core node | It is connected to everything, so it means nothing. | `Orb:107` | P1 |
| E2 | Family hubs (Memory, Skills, Tools, Apps, MCP) | They draw catalog membership, which the legend already shows. | `Orb:112`, `Orb:141` | P1 |
| E3 | Category and risk-tier sub-hubs | They add about 19 extra nodes and spokes. | `Orb:145`, `Orb:168`, `Orb:191` | P1 |
| E4 | Hub-to-leaf edges | They are styled exactly like real relationships. | `Orb:102`, `View:216` | P0 |
| E5 | The reactor (`ReactorCore`) | It reflects voice state only. `--orb-level` is set only by `DeckOrb.tsx:125-158`, so the core never pulses here **(inferred)**. It is `aria-hidden` and does not respond to clicks (`pointer-events-none`). | `View:298-301`, `ReactorCore.tsx:35` | P1 |
| E6 | The reactor's 60px cyan box-shadow | Pure decoration. | `View:299` | P3 |
| E7 | Per-node glow | Duplicates the alpha "live" signal. | `View:168-169` | P2 |
| E8 | The "Online" dot | It means "WebSocket connected" and nothing more. | `View:193`, `Server:1118-1123` | P0 |
| E9 | The model pill | It shows "UNKNOWN" or a stale model. | `View:307-310`, `Server:1126` | P0 |
| E10 | The "N things the assistant knows or can use" headline | It is inflated by hub nodes and ignores filters. | `View:241` | P0 |
| E11 | The orbiting starburst layout | Its geometry encodes nothing. | `View:142-144` | P1 |
| E12 | Tool "Runs freely / logged / asks" grouping | It is visible only as a hub label at zoom > 1.3. | `Orb:77-81`, `View:179` | P2 |
| E13 | The radial gradient background | Decoration. | `View:198` | P3 |
| E14 | The Apps family | It is the marketplace catalog shown as "accounts". | `Orb:187`, `Market:373` | P1 |
| E15 | The Tools family | It is a dot cloud of 91 tool ids with no link to what uses them. | `Orb:163-182` | P1 |
| E16 | The ask bar | It duplicates Chat and ignores the graph. | `View:343-375` | P1 |
| E17 | The click-zoom ×3 animation | It feels responsive, but the panel then tells you nothing. | `View:225-229`, `View:316-340` | P2 |
| E18 | The hover detail panel | It is one line of boilerplate ("Meta page in the wiki"). | `Orb:125` | P1 |
| E19 | The "Concepts" family | It is a whole filter and colour for a count of 1, because of missing classification. | `Orb:75`, `Wiki:206` | P1 |
| E20 | The "MCP servers" row at 0 | A filter for nothing. | `View:276-294` | P2 |

---

## F. Duplicate / overlapping functionality

| Area | Duplicates | Evidence | Classification |
|---|---|---|---|
| Memory graph surfaces | Memory Orb, the Notes map (`WikiGraph.tsx` 2D + `WikiGraph3D.tsx`) and the HUD's `DeckWiki.tsx` | `Nav:130`, `Nav:163`; `MainView.tsx:76-77`, `:109-110` | **Consolidate**: one memory graph component, one data adapter |
| Graph data adapters | `lib/orbGraph.ts` vs `lib/wikiGraph.ts` (with different colour meanings) | `Orb:53-61` vs `WG:89-102` | **Consolidate** onto one visual grammar |
| Camera/fit/layout | Orb uses plain `zoomToFit` while Notes uses `graphCamera`/`graphContinuity`/`graphForces` | `View:222`, `View:262` vs `Cam:96`, `Cont:66` | **Reuse** the existing libraries |
| Capability catalogs | Orb (Jarvis `/api/skills`, `/api/tools`, `/api/marketplace/plugins`, `/api/mcps`), Tool Armory (Hermes `/api/hermes/inventory`), Plugins/Skills/MCP section, Marketplace | `View:58-64`; `ArmoryHermes.tsx:145`; `Nav:137-151` | **Competing source of truth**: decide on Hermes as the authority |
| One integration, many nodes | App + `plugin-<id>` skill + tool (+ MCP tool) | `v2/3-orb-search.png`; `brain/manager.py:6272`; `Tools:17-21` | **Model as one entity** with facets |
| Chat input | Orb ask bar vs Chat vs HUD | `View:343-375`, `View:16` | **Remove** from the Orb, or make it contextual |
| Brain status | Orb fetch vs store `brainProvider`/`brainModel` vs sidebar footer | `View:113-117`, `Store:528-532` | **Single source** (store) |
| Connection / voice status | Orb "Online" vs sidebar "Voice unavailable" | `View:193`; screenshot `import/reimport_ui.png` | **Single status model** |
| Reactor visual | `ReactorCore` in the HUD (`DeckOrb.tsx`) and in the Orb | `View:15`, `View:300` | **Keep in HUD only** |
| Search | Orb client substring, `/api/wiki/search` FTS (`Wiki:679`), global sidebar search | `Orb:245` | **Consolidate** onto backend search |
| Import entry point | Orb "Import Data" vs Notes view import/setup dialogs (`WikiView.tsx:75`, inferred overlap) | `Import:128-135` | **Single import entry**, reachable from both |

## G–J. Remove / Simplify / Rebuild / Keep

| Feature | Decision | Why | Evidence |
|---|---|---|---|
| Core node + family hubs + category/tier hubs | **Remove** (G) | They are fabricated structure. Use layout regions or separate views instead. | `Orb:104-107`, `Orb:112`, `Orb:141-145`, `Orb:164-168`, `Orb:187-191`, `Orb:211` |
| Hub-to-leaf edges | **Remove** (G) | Only real relationships may draw edges. | `Orb:102` |
| Reactor in the Orb | **Remove** (G), unless it is given one explicit job (for example, voice listening state with a label) | Decorative and duplicated in the HUD. | `View:298-301` |
| "Online" dot + model pill | **Remove** (G) or **rebuild** as named statuses (Brain: provider/model; Voice: ready/unavailable; Memory index: ok/failed) with a tooltip and a source | Ambiguous. | `View:302-311`, `Server:1118-1126`, `Store:448`, `Store:528-532` |
| Ask bar | **Remove** (G), or **simplify** (H) to "Ask about the selected item" that sends the selection as context and links to the chat thread | Duplicate chat with a wrong reply heuristic. | `View:133-136`, `View:343-375` |
| Per-node glow, gradient wallpaper | **Remove** (G) | They carry no meaning. | `View:168-169`, `View:198` |
| Filters | **Simplify** (H): live counts, shown/total, disable rows with 0, explicit "All / None" | Currently cosmetic. | `Orb:240`, `View:276-294` |
| Headline count | **Simplify** (H): real items only, filtered and total, with a per-source status | Inflated. | `View:241` |
| Import Data | **Simplify** (H): move it to its own popover anchor that does not cover the filters, and refresh only the wiki source on completion | Overlap. | `Import:140`, `View:273`, `Import:83` |
| Graph data model (`orbGraph.ts`) | **Rebuild** (I): an entity model with `{id, kind, source, status, mtime, provenance}`, real edges only (wikilinks, MCP tool→server via `mcp_server`, app→tool, skill→app), and dedup before counting | Root cause. | `Orb:92-231`, `Tools:30-34` |
| Memory vs Capabilities | **Rebuild** (I) as two views in one shell: MEMORY (graph of notes and links), CAPABILITIES (grouped list/grid with status, from Hermes), and cross-links only where data proves them | Different semantics. | §1 |
| Search | **Rebuild** (I): backend FTS for memory plus the capability index; a results list; Enter focuses and frames the hits plus their neighbours; a "0 results" state; respects filters | Spec §12. | `View:129`, `View:165`, `Wiki:679-723` |
| Fit | **Rebuild** (I) on `graphCamera.framingFor` (95th-percentile framing), over **visible** nodes or search hits, with min/max zoom, re-run on filter and data change | Spec §18. | `View:262`, `Cam:5-9`, `Cam:96` |
| Layout | **Rebuild** (I): reuse `carryOverPositions`, add a collision force sized to radius plus label, a centring force for strays, and level-of-detail labels | Spec §10/§11. | `Cont:66`, `Forces:48`, `View:141-145` |
| Detail panel + Open | **Rebuild** (I): a pinned panel with kind, source path, mtime, backlinks/links (with `context`), and an Open that deep-links (`requestWikiPage(slug)`; skill/app/MCP by id) | Ship test 2–4. | `View:316-340`, `Store:731-736`, `Wiki:647-676` |
| Error/empty/loading states | **Rebuild** (I): per-source state (loading, ok, empty, unavailable, error) shown in the legend; treat `ok:false` as an error; show "Memory not configured" | Spec §23/§24. | `View:46-55`, `Wiki:577-585` |
| Parse cache + in-flight scan sharing | **Keep** (J) | Real performance work that is correct. | `Wiki:242-259`, `Wiki:378-405` |
| Template exclusion, placeholder-free titles | **Keep** (J) | It prevents phantom nodes and junk titles. | `Wiki:83-88`, `Wiki:169-181`, `Wiki:587-594` |
| Separate `broken[]` edge list | **Keep** (J) and start using it | Honest data. | `Wiki:574-575`, `Wiki:628-635` |
| Symlink containment in the vault walk | **Keep** (J) | Security. | `Wiki:284-288`, `Wiki:315-319` |
| Honest plugin liveness (`live_callable`, `runtime_missing`) | **Keep** (J) and consume it | Already truthful in the backend. | `Market:413-424` |
| Import job: progress, cancel, skip reasons, secret guard | **Keep** (J) | Clear and honest feedback. | `Import:45-62`, `Import:189-214`, `importer.py:396-400` |
| `aria-pressed` filters, `aria-label` on search/ask | **Keep** (J) | Baseline accessibility. | `View:250`, `View:271-280`, `View:363` |
| Pure, testable graph builder pattern | **Keep** (J) the pattern, but replace the tests that assert fake hubs | Good architecture habit. | `Orb:10-12`, `OrbTest:29-37` |
| `graphCamera` / `graphContinuity` / `graphForces` | **Keep** (J) and reuse them | They already solve Fit, continuity and stray nodes. | `Cam:5-9`, `Cont:4-13`, `Forces:5-11` |

---

## Spec questions answered directly

**What is the Orb for? (§16)** The reactor animates on `voiceState` and nothing else (`View:102`, `View:300`; `ReactorCore.tsx:9-14`). It is `aria-hidden` (`ReactorCore.tsx:35`) and not interactive (`View:298`). It does not represent memory, the selection or connection state. **Verdict: decorative. Remove it from this view.**

**What do ONLINE / UNKNOWN mean? (§17)**
- ONLINE is `connected && Boolean(brain?.provider)` (`View:193`). Provider is never empty, because the server falls back to the configured primary or the string `"unknown"` (`Server:1118-1123`). ONLINE therefore means only "the UI's WebSocket is connected". It says nothing about the brain being reachable, Hermes, voice, MCP or memory.
- UNKNOWN is the model fallback string when the active provider's config has no `model` (`Server:1124-1126`), uppercased by CSS (`View:302`).

**Verdict: replace it with named statuses taken from the store, or remove it.**

**Does Fit work? (§18)** Fit calls `zoomToFit(600, 60)` over all shown nodes (`View:262`). It ignores the search, frames outliers (the problem described in `Cam:5-9`), has no minimum readable zoom, and does not re-run on filter or data change (the `fitted` one-shot, `View:99-100`, `View:219-223`). Since Notes-hidden concepts drift (B9), Fit then frames the drifted node **(inferred)**. **Verdict: broken by design.**

**Are the filters real? (§13)** They do change the graph: nodes and links are removed (`Orb:234-242`). They do not change counts (`Orb:240`), the headline (`View:241`) or search (`View:129`), and hiding Notes breaks Concepts (B9). **Verdict: half real. Fix them or replace them with views.**

**Are the counts real? (§14)** Per-group counts tally real API rows (not hardcoded), but they are inflated by duplicates (C6), report failures as 0 (C3), include the catalog rather than what is connected (C13), and include invalid skills. The headline includes hubs (C2). **Verdict: not authoritative.**

---

## Priority matrix

Do not start P3/P4 until P0/P1 are closed. Order follows the spec: data model, search, filters, counts, Orb purpose, status, Fit.

### P0: broken / misleading / blocks use
1. **Data model**: remove fabricated hub nodes and edges; draw only real relationships; style hub-like grouping as layout regions, never as edges. Evidence: `Orb:104-107`, `Orb:129`, `View:216-217`; tests at `OrbTest:29-37` (C1, A4, E4).
2. **Data model**: treat `ok:false` as an error, and stop `wiki.nodes` crashing on it. Evidence: `Wiki:583-585`, `View:49`, `Orb:111` (C4).
3. **Data model**: a failed source must show "unavailable", never 0. Evidence: `View:46-55` (C3).
4. **Capability source of truth**: pick Hermes (as the Tool Armory already does) or explicitly reconcile with Jarvis registries. Evidence: `View:58-64` vs `ArmoryHermes.tsx:145` (C15).
5. **Search must focus**: frame the hits, show a result count and list, and give a "0 results" state. Evidence: `View:129`, `View:165`, `View:180` (B1, B2, B3).
6. **Filters must update counts and must not orphan nodes.** Evidence: `Orb:112`, `Orb:236-240`, `View:241`, `View:292` (B8, B9).
7. **Counts**: the headline counts real entities only, and dedup happens before counting. Evidence: `View:241`, `Orb:116` vs `Orb:98-100` (C2, C6).
8. **Status**: remove or replace ONLINE/UNKNOWN with named, store-sourced statuses consistent with the sidebar. Evidence: `View:193`, `View:307-310`, `Server:1118-1126`, `Store:448` (C17, C18, B19).
9. **Open must open the item.** Evidence: `View:327-334` vs `Store:731-736` (B7).
10. **Brightness must have one meaning**: separate "not live" from "not matched". Evidence: `View:167` (A9).

### P1: major UX problems
- Rebuild Fit on `graphCamera` over visible nodes or hits, with zoom limits. Evidence: `View:262`, `View:201-231`, `Cam:96` (B16, B20).
- Position continuity across filter and data changes. Evidence: `View:122-128`, `Cont:66` (B20, D1, D2).
- Split MEMORY and CAPABILITIES. Apps means connected accounts, not the catalog. Link MCP tools to their servers. Evidence: `Orb:187`, `Market:373`, `Tools:30-34` (C12, C13, E14, E15).
- Remove the reactor from the Orb. Evidence: `View:298-301` (E5, A12).
- Semantic node size (backlinks/recency) and non-colour encodings (shape/icon). Evidence: `View:40-44`, `View:170-173`, `WG:210-212` (A2, A3).
- Collision force plus label LOD and collision, with a minimum on-screen label size. Evidence: `View:176-188`, `View:142-144` (A5–A8).
- One shared colour grammar across Orb, Notes and HUD. Evidence: `Orb:53-61` vs `WG:89-102` (A11).
- A detail panel with relations, backlinks, source, mtime and "why here". Add `mtime` to the graph payload. Evidence: `View:316-340`, `Wiki:603-609` (B12, C10).
- Classify imported pages (kind) or drop the Concepts split. Evidence: `Wiki:204-206`, `Orb:75` (C9, E19).
- Surface the backend's duplicate-slug drop and fix edge misattribution. Evidence: `Wiki:598-602`, `Wiki:613-627` (C7).
- Loading, empty and "memory not configured" states. Evidence: `View:241`, `Wiki:577-579` (B17, C5).
- Import dialog must not cover the filters. Evidence: `Import:140`, `View:273` (A13).
- Search uses backend FTS and ignores boilerplate details. Evidence: `Orb:249-250`, `Wiki:679-723` (B4, B5, B6).
- Ask bar: remove it, or make it contextual with a correct reply association. Evidence: `View:133-136` (B18).
- Keyboard navigation and Escape. Evidence: `View:201-231` (B15).
- Tests for failure paths, filters, counts and search. Evidence: `OrbTest:28-72` (C20).

### P2: major polish / performance
- Shadow blur, glow and the `messages` subscription re-rendering the view. Evidence: `View:105`, `View:168-169` (D3–D5).
- Use the `/brief` endpoints and stop shipping edge `context` that is unused. Evidence: `View:60-61`, `Wiki:619` (D6, D8).
- LOD rendering, charge scaling and progressive loading. Evidence: `View:141`, `View:162-191` (D11, D13, D18, D19).
- Instrumentation for layout and render time (D20).
- Filter state affordance, hover/pick panel distinction, click-to-frame-neighbourhood. Evidence: `View:280-285`, `View:225-229` (B11, B13, B14).
- Naming consistency (Memory/Notes) and the MCP row at 0. Evidence: `Orb:64`, `Orb:112`, `View:276-294` (A16, A18).
- Answer bubble overflow. Evidence: `View:345-350` (A19).

### P3: minor polish
- Remove the gradient wallpaper (`View:198`).
- i18n strings (`View:238` etc.).
- Debounce search (`View:129`).
- Tune `cooldownTicks` (`View:218`).
- Initial `useSize` (`View:75`).
- Import poll scope (`Import:83`).
- Fetch `/api/skills/brief` (`Skills:355`).

### P4: optional
- 3D mode, timeline ("what changed this week") once `mtime` exists, saved views, and a mini-map.

---

## Ship test (spec §32) against the current code

| # | Ship test item | Status | Evidence |
|---|---|---|---|
| 1 | - [ ] Find a specific memory | **FAIL**: highlights by title substring only; no camera focus, no result list, no content search | `View:129`, `View:165`, `Orb:245-252` |
| 2 | - [ ] Understand why it appears | **FAIL**: the panel says "<Kind> page in the wiki" | `Orb:125`, `View:316-340` |
| 3 | - [ ] See related memories | **FAIL**: no relations or backlinks in the panel; edges have no meaning and are indistinguishable from hub spokes | `View:216-217`, `View:316-340` |
| 4 | - [ ] Open the source | **FAIL**: Open goes to the section root; the slug is unused | `View:327-334`, `Orb:128` |
| 5 | - [ ] Search skills | **PARTIAL**: substring highlight only, no focus | `View:165` |
| 6 | - [ ] Search tools | **PARTIAL**: same, and MCP/plugin tools are duplicated | `Tools:17-21` |
| 7 | - [ ] Search apps | **PARTIAL**: matches the catalog rather than connected apps; "connected" matches every app | `Orb:201` |
| 8 | - [ ] Search MCP servers | **PARTIAL**: highlight only; status may be wrong at boot | `Orb:214`, `Mcp:184` |
| 9 | - [ ] Filter correctly | **FAIL**: counts static; Notes-off orphans Concepts; search ignores filters | `Orb:240`, `Orb:112`, `View:129` |
| 10 | - [ ] Zoom without losing readability | **FAIL**: leaf labels only above 2.6×, 8.5px; no collision; no zoom limits | `View:176-188` |
| 11 | - [ ] Pan without losing context | **FAIL**: relayout from scratch on filter or data change | `View:122-128` |
| 12 | - [ ] Fit intelligently | **FAIL**: frames all nodes and outliers; ignores search; one-shot auto fit | `View:262`, `View:219-223` |
| 13 | - [ ] Understand every colour/state | **FAIL**: colour-only, double-meaning dimming, palette contradicts the Notes map | `View:167`, `Orb:53-61`, `WG:89-102` |
| 14 | - [ ] Understand every status indicator | **FAIL**: ONLINE equals WebSocket only; UNKNOWN is a fallback string | `View:193`, `Server:1126` |
| 15 | - [ ] Recover from loading/error/offline | **FAIL**: failures shown as 0; `ok:false` likely crashes the view; no retry | `View:46-55`, `Orb:111` |
| 16 | - [ ] Use without reading documentation | **FAIL**: hubs, colours and edges are unexplained | `Orb:104-107` |

Result: **0/16 pass, 4 partial.**

---

## Could not verify (needs a runtime pass)

- Real-world FPS, layout time, memory, CPU and GPU at 2.5k nodes. Every item in D is a reasoned risk, not a measurement.
- The 2,553 / 2,357 figures: the screenshots available here show small test vaults (180 and 174 real items). The arithmetic of 24 hubs plus 2529 items is derived from the spec's numbers and the code.
- The exact crash behaviour when `/api/wiki/graph` returns `ok:false`. A TypeError at `Orb:111` is certain from the code; whether `ViewErrorBoundary` catches it for this section is assumed.
- How often slugs collide in the user's real imported vault. The mechanism is certain (`Wiki:598-602`); the frequency is unknown.
- Whether `messages` updates on every streaming token, which determines how bad D4 is.
- Which provider config produces "UNKNOWN" for this user. The mechanism is `Server:1124-1126`.
- Browser zoom (125/150%), 4K, narrow-window layout, and contrast ratios were not tested.
- The rest of the UI (spec §21: Home, Chat, Voice, Browser, Computer Use, Settings, and others) is out of scope for this document.

## Final question (spec, last section)

Someone who has never seen the code would see a **developer prototype**: a force-directed dump of five API lists around a decorative reactor, with a status pill that can read "UNKNOWN". **NOT SHIP-READY.**
