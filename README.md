<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast — stealth fork ⚡

> [!IMPORTANT]
> **This is a fork of [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast).**
> The decision loop, indexed action space, and one-request policy are upstream's. This fork changes
> where and how the agent browses:
>
> - **[CloakBrowser](https://github.com/CloakHQ/CloakBrowser) stealth browsing by default** — a
>   headed stealth Chromium with human-like mouse curves and per-character typing. Set
>   `JEV_BROWSER=chrome` for the upstream direct-CDP path.
> - **Three interchangeable decision backends**: OpenRouter (no TypeSafe account needed), the
>   official TypeSafe API, or a fully local [Laya](https://github.com/NandhaKishorM/laya) server.
>
> Timing claims below are upstream's, measured on the direct-CDP path. Humanized stealth runs trade
> speed for behavior — expect seconds per action instead of milliseconds.

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an
element. A small LLM writes text only when the operation is `TYPE_TEXT`.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every page observation builds an indexed table of accessible elements and their current values:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and
`BLOCKED`. Only supported operations and targets are offered.

```text
                      one decision request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two
decisions, **one network round trip**. Each target head contains only compatible elements. Native
dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights
example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels
afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/Peterhungchien/jev-ultrafast.git
cd jev-ultrafast
uv sync --extra stealth
cp .env.example .env
uv run jev
```

Open **http://127.0.0.1:8766**, choose a preset or enter any HTTP(S) URL in **Open URL**, then
click **Start demo → Run automatically**. Changing the preset restores its default URL and goal. The
inspector shows numbered elements, operation probabilities, target probabilities, and executed
actions. **Choose next** pauses before execution.

### Decision backend — pick one

**OpenRouter (quickest; no TypeSafe account).** Create a key at
[openrouter.ai](https://openrouter.ai/keys), then in `.env`:

```bash
TYPESAFE_API_KEY=sk-or-v1-...
TYPESAFE_BASE_URL=https://openrouter.ai/api
```

**Official TypeSafe API.** Get a key at [console.typesafe.ai](https://console.typesafe.ai) and set
`TYPESAFE_API_KEY` — leave `TYPESAFE_BASE_URL` unset.

**Fully local (free).** Install the open-source [Laya](https://github.com/NandhaKishorM/laya)
engine, which speaks the same `/v1/systemone` protocol:

```bash
pip install "laya[serve]" && laya-serve
# in .env: TYPESAFE_API_KEY=local-laya and TYPESAFE_BASE_URL=http://127.0.0.1:8000
```

### Text helper

`TEXT_MODEL_*` configures the small OpenAI-compatible model that writes field values (required for
`TYPE_TEXT`). `.env.example` uses `inception/mercury-2.5` through OpenRouter; the code defaults to
DeepSeek when those settings are omitted. Gemini, GLM, and any OpenAI-compatible endpoint also work
— set `TEXT_MODEL_API_KEY`, `TEXT_MODEL_BASE_URL`, `TEXT_MODEL`, and `TEXT_MODEL_REASONING`.

### Browser — stealth by default

CloakBrowser is an optional package extra so the core executor remains usable without stealth
imports: install with `uv sync --extra stealth` (or `pip install 'jev-ultrafast[stealth]'`). This
fork's browser factory selects it by default; `JEV_BROWSER=chrome` keeps the dependency-free
upstream path.

The first stealth run downloads the CloakBrowser binary and opens a headed window; jev's tab, the
snapshot reads, and the occlusion guards run there, while a humanized Playwright layer drives
clicks and typing (Bézier mouse curves, per-character keystrokes). Knobs in `.env`:

| Variable | Effect |
| --- | --- |
| `JEV_BROWSER=chrome` | Upstream path: your regular Chrome, instant CDP input |
| `JEV_HEADLESS=1` | Headless stealth window (much slower with humanize; headed recommended) |
| `JEV_HUMANIZE=0` | Stealth fingerprints without behavioral humanization |
| `JEV_PROXY` / `JEV_GEOIP=1` | Residential proxy (+ matching timezone/locale) for hard targets |
| `JEV_CDP_PORT` | DevTools port for the stealth browser (default 9242) |

For bot-checked sites, add a residential proxy and `JEV_GEOIP=1` — CloakBrowser's own hard-target
recipe.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness),
installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote
debugging in Chrome when prompted (chrome mode only; stealth mode attaches over its own port).

## Use the library

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on December 1, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Stealth browsing is on by default; export `JEV_BROWSER=chrome` for the upstream direct-CDP path.
Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks
the actual route/date/results, and saves its trace. It does not select or book a flight.

### Parallel workers

Multiple `Agent` objects can own target-bound tabs in one process, but those tabs share one browser
profile, fingerprint, proxy, cookies, and storage. Use separate processes when jobs need independent
browser identities or stronger failure isolation. Every stealth process needs a unique
`BU_NAME` (browser-harness daemon) and `JEV_CDP_PORT`, set before importing `jev_ultrafast`.

[`examples/parallel_processes.py`](examples/parallel_processes.py) assigns both values, launches all
workers concurrently, and stops their named harness daemons. This debugging run opens headed
browsers, records each observation, and keeps completed browsers alive until Enter is pressed:

```bash
uv run --env-file .env python examples/parallel_processes.py \
  --record-dir artifacts/parallel --keep-open \
  --job https://en.wikipedia.org/wiki/Main_Page \
    'Open the article about Gödel’s incompleteness theorems.' \
  --job https://example.com \
    'Stop when the Example Domain page is visible.'
```

`--record-dir` creates `worker-01/`, `worker-02/`, and so on, each containing timestamped JPEG
frames and a final `state.json` trace. `--keep-open` waits for every successful task, activates its
completed tab, leaves the browser and CDP endpoint available for inspection, then closes them when
Enter is pressed. Add
`--headless` when windows are not needed; headless targets can still be inspected through the CDP
ports printed at startup. Use `--base-port` if the default range starting at 9242 is occupied, and
`--dry-run` to inspect assignments without launching workers.

Worker output streams live instead of being buffered. Every stdout line is prefixed with its source,
for example `[worker 2]  1840 ms  3 actions  ready  Search`, while errors use
`[worker 2 stderr]`. These prefixes keep interleaved concurrent logs attributable to the right job.
Workers inherit model credentials and browser settings from the parent, and each worker makes its
own real model calls; account for provider concurrency limits and cost. The local inspector remains
intentionally single-agent and serializes its commands.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts
  into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text
  atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby
  context. Animation alone does not force another prediction. Resolve current geometry and reject
  covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at
  200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after
  execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without
  switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the
  entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and
click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable
JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Generic browser connection, guards, and pluggable input dispatch |
| [browser_factory.py](jev_ultrafast/browser_factory.py) | Fork browser selection (`stealth` or `chrome`) |
| [stealth.py](jev_ultrafast/stealth.py) | Optional CloakBrowser session and humanized input worker |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |

## Evidence and limits

Upstream measured a **7,073 ms** Google Flights run on the direct-CDP path with the official API.
In six alternating upstream runs with identical models and settings, both versions passed **3/3**;
median task time went from **9.450 s → 7.092 s**, a **25% reduction**, and median browser protocol
calls went from **1,092 → 101**. That is three repeats of one task on one browser profile, not a
general reliability benchmark — and it describes the `JEV_BROWSER=chrome` path, not humanized
stealth runs. Upstream's measurements and raw traces are in
[docs/performance.md](docs/performance.md).

This fork's stealth mode intentionally trades that speed for human-like behavior: expect seconds
per input action. The same policy opened Wikipedia in **2.798 s** and passed a local hotel
search/filter task in **1.896 s** upstream.

A `DONE` choice still requires independent outcome verification. The DOM reader handles common
HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas,
uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP.
In stealth mode, tabs live in a dedicated CloakBrowser profile; in chrome mode they share the
existing Chrome profile.

## Development

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser
without model calls. Live examples and recording scripts make paid API calls.

## Credits

- Upstream project, decision loop, and measurements:
  [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast)
- [TypeSafe AI](https://typesafe.ai) — the Jev System One model and API
- [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) — stealth Chromium and humanized input
- [Laya](https://github.com/NandhaKishorM/laya) — local System One decision engine
- [OpenRouter](https://openrouter.ai) — hosted Jev access without a TypeSafe account
- [Browser Harness](https://github.com/browser-use/browser-harness) ·
  [Browser Use](https://github.com/browser-use/browser-use)
