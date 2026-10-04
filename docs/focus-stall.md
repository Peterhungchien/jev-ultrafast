# Headed stealth: apparent agent stall on an inactive workspace

Issue [#3](https://github.com/Peterhungchien/jev-ultrafast/issues/3) describes an agent
that stops producing the next action until the browser's window focus changes. This
is not necessarily a frozen browser or a model request that never returns.

## Finding and fix

On this Linux niri/Wayland desktop, CloakBrowser's mouse-movement acknowledgements
slowed to about **one second per point** while its headed window was on an inactive
workspace. The humanized curve sends dozens of points synchronously, so a single
agent input action took 26–81 seconds. Predictions had already returned; the agent
could not request its next decision until input finished.

The existing focus emulation and these flags were insufficient:

- `--disable-backgrounding-occluded-windows`
- `--disable-renderer-backgrounding`
- `--disable-background-timer-throttling`

Adding **`--disable-frame-rate-limit`** to headed stealth launch restored normal
mouse acknowledgements and animation frames without activating the window. Chromium's
[begin-frame implementation explanation](https://chromium.googlesource.com/chromium/src/+/f6826db324b55882a9079bdbe9d54c8875f3235f)
describes this flag's back-to-back frame source, which does not wait for display vsync.
This supports the observed frame-scheduling mechanism, not a guarantee across desktops.

The fix leaves Playwright input, humanized curves, per-character typing, target
freshness, and occlusion guards unchanged. It does not retry mutations or require
screenshots. The flag is headed-only; headless launch is unchanged. Keeping offscreen
rendering active may increase CPU/GPU use compared with throttled rendering.

## Local controlled-page evidence

Environment: Linux x64, niri/Wayland, CloakBrowser Python 0.5.11, bundled Chromium
146.0.7680.177.5, Playwright 1.63.0; headed, humanization enabled, no browser proxy.
Each run used a newly launched browser and a local HTTP fixture. Only its new native
window was moved to an inactive workspace **before initial navigation**. The fixture
independently asserted the typed value and the click's resulting DOM text. Workspace
and focus state were checked before and after inputs. No models were called.

| Configuration | Humanized fill | Humanized click | rAF frames / 0.5 s |
| --- | ---: | ---: | ---: |
| No backgrounding/frame flags; normal Playwright | 15.057 s | 25.800 s | 0 |
| Backgrounding flags only; direct-CDP mouse probe | 21.623 s | 26.019 s | 0 |
| Backgrounding + frame flag; direct-CDP mouse probe | 2.552 s | 0.735 s | 28 |
| Backgrounding + frame flag; normal Playwright | 3.363 s | 0.682 s | 29 |

The direct-CDP probe was diagnostic only and is **not** part of the shipped fix.
Its raw per-point timings changed from approximately 1.016 s to approximately
0.017 s. Randomized paths and typing pauses differ between runs; these are individual
reproductions, not a statistical benchmark.

Raw output:

- [No backgrounding flags](evidence/focus-stall/local-without-background-flags.log)
- [Backgrounding flags, CDP probe](evidence/focus-stall/local-background-flags-cdp.log)
- [Frame flag, CDP probe](evidence/focus-stall/local-frame-flag-cdp.log)
- [Frame flag, unchanged Playwright input](evidence/focus-stall/local-frame-flag-playwright.log)

## Full agent-loop check with real models

Both Hitachi runs used the configured decision backend and text helper, the same goal,
and an unfocused window on an inactive workspace. Each was capped at six ticks and
produced **six decisions and five executed actions**. One stale prediction was consumed
without input in each run. Each run invoked the text helper once; provider requests
inside the helper can include fallbacks and are not counted by these phase logs.

| Phase | Backgrounding flags only | With headed frame flag |
| --- | ---: | ---: |
| First click execution | 44.264 s | 0.732 s |
| Text execution (excluding helper) | 80.733 s | 2.723 s |
| Next click execution | 26.327 s | 0.690 s |
| Decision requests | 0.316–1.129 s | 0.280–1.109 s |
| Text helper | 11.004 s | 11.291 s |
| Initialization through six ticks, excluding cleanup | 173.482 s | 25.778 s |

Before the fix, watchdog stacks found the agent waiting in `HumanInput.move()`.
After it, there were no 40-second watchdog reports, and next-action prediction
continued without manual window-focus changes.

These checks verify the **input-delay / missing-next-action symptom on this desktop**,
not completion of the Shanghai job search. Both traces ended `ready` at the diagnostic
cap, not `done`; Shanghai listings were not independently verified. Model choices,
autocomplete handling, and enhancement #2 remain separate concerns.

- [Agent loop before the frame flag](evidence/focus-stall/hitachi-background-flags.log)
- [Agent loop after the frame flag](evidence/focus-stall/hitachi-frame-flag.log)

## Harnesses and reproduction

The diagnostic harnesses are retained as text, separate from the offline test suite.
They require this project's stealth extra, a running niri desktop, and an otherwise
unused diagnostic CDP port. They affect only a newly identified test browser window,
restore the original workspace, and shut down the owned browser and named daemon.
They write temporary event/state files under `/tmp`; the live harness calls paid APIs.

```bash
cp docs/evidence/focus-stall/local_harness.py.txt /tmp/jev-focus-check.py
cp docs/evidence/focus-stall/mouse_probe_hook.py.txt /tmp/jev-mouse-probe-hook.py
# Patched, normal input; assert advancing frames and each phase under 10 seconds:
EXPECT_FRAMES=1 PYTHONPATH=. uv run python /tmp/jev-focus-check.py
# Baseline without backgrounding/frame flags (do not assert advancing frames):
FOCUS_BASELINE=1 PYTHONPATH=. uv run python /tmp/jev-focus-check.py
# Optional diagnostic CDP transport, not a production change:
MOUSE_PROBE=cdp PYTHONPATH=. uv run python /tmp/jev-focus-check.py

# Paid model calls; same six-tick Hitachi check:
cp docs/evidence/focus-stall/live_harness.py.txt /tmp/jev-agent-focus-check.py
PYTHONPATH=. uv run --env-file .env python /tmp/jev-agent-focus-check.py
```

The committed baseline toggle also removes the newly added frame flag so it can
reproduce the pre-fix configuration on the patched tree. Original logs were collected
before that flag was shipped. Minimization was not tested: niri did not honor the
requested minimized state. Other compositors, platforms, and browser builds still
require their own validation.
