# Dynamic operation + target

The input is a natural-language goal. Every page observation builds an indexed table of accessible elements and their current values. One node receives one index, even when it supports both clicking and typing.

One TypeSafe request asks which operation to perform and which target would be appropriate for each available operation. The executor consumes only the target head corresponding to the selected operation. This avoids serial operation-then-target calls and rejects targets incompatible with the operation. Dropdown targets include a code-owned option index.

Operation and target questions receive the same next-step rules. Target criteria include current values and checked/selected state. The questions run independently: a target cannot read the operation answer, so its premise explicitly names the operation it assumes.

TYPE_TEXT sends the goal, selected field, visible page context, and recent actions to a small LLM. Its JSON must contain exactly one valid `text` value. The code does not extract quoted literals. A value can be reused after a stale decision only while the entire helper input is identical, and is discarded after a successful mutation.

## Runtime

One browser-side DOM snapshot supplies common HTML/ARIA roles, names, values, visible text, and executable targets. It also admits up to 80 labeled, visible non-semantic controls whose own `tabindex`, `onclick`, or pointer cursor indicates interaction, while excluding wrappers around semantic controls. A WeakMap gives each actual node a code-owned identity; a Map keeps the live references used for execution. Replaced elements receive new identities, disconnected references are pruned, and navigation starts a new cache. These IDs are not CDP backend node IDs. Geometry is always read again immediately before input.

The model sees visible text. Background focus emulation keeps animation frames running in the owned tab. Screenshots are optional and disabled in library calls by default; `screenshots=True` or `record_dir=...` enables them. The inspector enables them explicitly. A continuous screencast can record a run separately.

Freshness compares semantic state instead of counting DOM mutations. Before every node-bound operation (`CLICK`, `TYPE_TEXT`, `SUBMIT`, or `SELECT`), guards compare document identity, full URL, viewport, selected target, and nearby form/dialog/row text plus safe field values/states. Scrolling, waiting, and completion use a full semantic comparison. The executor rechecks target visibility, enabled state, geometry, and click occlusion. The trace records both full `page_changed` and scoped `relevant_changed`; no-progress detection uses the latter so an unrelated carousel cannot make a no-op look successful. Scoped guards intentionally permit unrelated visible content to change; this is a practical heuristic, not proof that arbitrary page changes are irrelevant to the goal.

Browser mutations are not retried by transport recovery. Initial `Page.navigate` gets a 30-second daemon-response budget for slow DNS or proxy negotiation, but it still executes only once and constructor failure closes the owned target. Completed execution is logged before the next observation, including when that observation encounters a navigation. An interrupted native-select evaluation stops because its change event may already have fired. Typing uses a browser select-all command followed by CDP text insertion, so existing input contents are replaced. Populated single-line inputs expose a generic `SUBMIT` action that focuses the same guarded node and presses Enter; it does not guess selectors or call a page form directly.

The core `Browser` accepts an optional input-dispatch factory and has no CloakBrowser imports. Without a factory, validated clicks, typing, and wheel events use direct CDP input. This fork's separate browser factory selects the optional stealth adapter by default; `JEV_BROWSER=chrome` constructs the generic direct-CDP browser unchanged. The stealth adapter connects a humanized Playwright client to the exact owned target. It uses `JEV_PROXY` when set, otherwise inherits standard proxy variables, while forcing loopback control traffic into `NO_PROXY`. Pointer movement completes before the primary session rechecks node identity, geometry, and occlusion; mouse-down happens only after that recheck. Snapshot reads, guards, and native dropdown handling stay on the primary session. After execution is logged, an opened page is adopted only when its CDP `openerId` belongs to the code-owned target chain; unrelated tabs are ignored, the input worker is rebound, and all owned targets are closed together.

The next observation waits for up to two animation frames or 50 ms after an interaction. Editable ARIA comboboxes instead wait for visible options, capped at 200 ms. This avoids paying for a prediction before autocomplete suggestions arrive. An explicit WAIT remains 100 ms; network loading is never fast-forwarded in the recording.

## What changed after the first demo

The initial prototype used five manually prepared steps and copied quoted strings. That proved finite-choice browser execution but did not demonstrate task decomposition or text generation. The current policy removes that shortcut and uses the original goal throughout. Operation/target distributions replace the old flat-choice/lookahead/Noul arrangement.

The audit also found that treating every INPUT as editable misclassified checkboxes. Editable roles now control TYPE_TEXT availability. Tests cover checkbox/radio/button distinction, invalid operation/target outputs, stale decisions, text-cache invalidation, missing credentials, waits, and final-route verification.

## Boundaries

Sixty browser actions and 120 decision requests bound a run. Up to 250 action candidates are retained; truncated candidates cannot be selected. The service stays loopback-only, exposes one agent, serializes inspector actions, and checks Host, Origin, and a local request token. Its configurable start URL accepts only credential-free HTTP(S) URLs; scenario URLs remain defaults for older clients. Credentials remain server-side. In the default stealth mode, tabs in one process share a dedicated CloakBrowser profile; with `JEV_BROWSER=chrome`, tabs share the existing Chrome profile. Independent stealth processes require distinct browser-harness names and CDP ports; the parallel-process example assigns both before importing the browser stack.

The policy is generic, but a few websites do not establish broad reliability. Name resolution covers common labels, ARIA references, text, and a bounded pointer-control fallback; it is not the browser's full accessibility algorithm. Shadow roots, frames, canvas, uploads, nested scrolling, non-page pop-ups, and complex keyboard interactions can block progress. A valid action can still be wrong. Independent checks, rather than the model's DONE choice, determine whether the demonstrated task succeeded.
