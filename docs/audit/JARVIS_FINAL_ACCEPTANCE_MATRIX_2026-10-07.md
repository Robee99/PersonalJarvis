# Final acceptance matrix (2026-10-07)

Every scenario from the master prompt's real-Windows list (section 34), with
how it is proven. **Auto** = `jarvis system acceptance` drives it through the
running app and judges it from something other than the reply where it can
(Windows' brightness, the Notepad process, the chat's tool events, a known web
page, a code word only the conversation knows). **You** = needs a person at the
PC (a microphone, a camera, ears). Results go in
`JARVIS_HERMES_PARITY_CERTIFICATION_2026-10-07.md`.

## Real Windows scenarios

| # | Scenario | How | Check id | Pass means |
|---|---|---|---|---|
| 1 | Wake Jarvis | You | — | wake word starts listening |
| 2 | "Open Camera" | You | — | Camera app opens |
| 3 | "What am I holding?" | You | — | answer names the object |
| 4 | "What is on my screen?" | Auto | `screen` | reply contains the word typed into Notepad in step 6 |
| 5 | "Open Notepad" | Auto (spoken) | `notepad` | a new Notepad process, or the screen shows the typed word |
| 6 | Type something | Auto (spoken) | `notepad` | as above |
| 7 | Open browser | Auto | `web-page` | heading of example.com read back |
| 8 | Search web | Auto | `web-search` | names IANA |
| 9 | Scrape a real page | Auto | `web-page` | "Example Domain" |
| 10 | Structured extraction | Auto | `web-extract` | JSON with the heading and link text |
| 11 | Browser click | Auto | `web-click` | lands on iana.org |
| 12 | "Don't click anything" | Auto | `no-click`, `no-action` | no tool runs, no approval asked |
| 13 | Complex research question | Auto (partial) | `web-search` | a real search answered; depth judged by you |
| 14 | Delegate a long task | Auto | `interrupt` | the turn starts running |
| 15 | Interrupt it | Auto | `interrupt` | turn ends ≤ 5 s after stop |
| 16 | Resume conversation | Auto | `resume` | next turn recalls the code word |
| 17 | Type a follow-up | Auto | `chat-recall` | typed turn recalls the code word |
| 18 | Speak a follow-up | Auto (spoken path) | `voice-continuity` | spoken turn recalls the typed code word |
| 19 | Same-session context | Auto | `voice-continuity`, `resume` | as above |
| 20 | Brightness to 30 % | Auto (spoken) | `brightness` | Windows reads back 30 ± 5 % within 2 s; old level restored |
| 21 | Verify actual brightness | Auto | `brightness` | read from WMI, not from the reply |
| 22 | Honest failure | Auto | `brightness` | a screen without WMI brightness is SKIP with the reason, never a claimed change |

"Spoken path" means the turn goes through the same brain call the speech
pipeline makes after recognition (`/api/acceptance/spoken-turn`); the
microphone, recognition and the voice itself are the **You** rows below.

## Capability rows (section 33)

| Area | Auto | You |
|---|---|---|
| Text | basic, follow-up, context (`chat-turn`, `chat-recall`), memory (`memory`) | long conversation |
| Voice | voice↔text continuity (`voice-continuity`), interruption (`interrupt`) | wake, STT accuracy, TTS heard, barge-in by speaking |
| Vision | screen (`screen`) | camera, image, OCR, visual computer task |
| Computer | launch app, type (`notepad`), safety (`no-click`, `no-action`), cancellation (`interrupt`) | click, scroll, window management |
| Browser | static page, extraction, click, search (`web-*`) | dynamic/JS page, screenshot, authenticated page, prompt injection |
| Skills / MCP / plugins | `hermes` (computer_use toolset on) | install, enable, disable from the Tool Armory |
| Memory | import + recall (`memory`, a fresh marker each run) | update, delete, live watcher |
| Delegation | delegate, cancel (`interrupt`) | completion, failure |
| Code | — | generate, execute, fix, verify |
| Recovery | — | Hermes restart, model failure, network loss, voice failure |
| Latency | median first text ≤ 5 s (`latency`), reflex ≤ 2 s (`brightness`) | cold and warm start |

## Approvals during the run

The run never says yes. When Hermes asks for approval the run answers "no",
records the ask, and marks the check FAIL where no action was wanted
(`no-action`, `no-click`) or SKIP where an action was (`notepad`: say it
yourself and answer yes).
