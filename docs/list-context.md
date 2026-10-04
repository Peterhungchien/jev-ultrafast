# Bounded read-only list context

Enhancement [#2](https://github.com/Peterhungchien/jev-ultrafast/issues/2) concerns
long result lists: an action table showing two viewport-visible links can obscure
that ten rendered rows exist farther down the page. Pagination is not proof that
all requested results have been inspected.

The action table remains viewport-only. The same atomic snapshot now also returns
`list_context`, sent as page data in the existing single decision request:

```json
{
  "groups": [{
    "label": "Jobs",
    "rendered_rows": 10,
    "visible_rows": 2,
    "rows_below_viewport": 8,
    "uninspected_rows": 0,
    "reported_total": null,
    "preview": [{"position": 3, "text": "A rendered row below the fold"}],
    "omitted_preview_rows": 0
  }],
  "omitted_groups": 0
}
```

- Groups use generic list/table/ARIA list markup, or repeated semantic article/row
  children. No site selectors, prepared field strings, or additional model requests
  are involved.
- Only groups intersecting the viewport are eligible. Preview rows are restricted
  to the next viewport below the fold. Ordinary offscreen prose and navigation,
  footer, header, aside, menu, and listbox groups are excluded.
- Up to four groups and 200 candidate rows per group are inspected. Rendered counts
  include only non-hidden, nonzero-size inspected rows. `uninspected_rows` records
  remaining DOM candidates; they may include hidden rows and are not claimed as
  inspected results. An available numeric ARIA total is reported separately and is
  not independently verified.
- There are at most six previews per group, 240 characters per preview, and 2,400
  preview characters across all groups. `position` is the DOM candidate-row position,
  **not an action/element index**. Preview records contain no node IDs or coordinates.
- Offscreen preview rows never enter the action table or operation-specific targets.
  Scroll to make a needed target observable before acting. The policy treats previews
  as read-only hints, not visible completion evidence, and does not infer enumeration
  completeness from pagination or bounded counts.
- List context participates in the page fingerprint and semantic marker, so a change
  invalidates whole-page/DONE freshness. Action-specific guards still permit an
  unrelated preview change when the selected visible target remains fresh.

This does not eliminate every scroll or support nested scrolling/virtualized list
items absent from the DOM. For enumeration, state the desired number or coverage in
the goal, rather than merely asking to “surface” results.

## Validation

The offline suite contains 77 passing tests, including local cached-Chromium DOM
checks for list counts/look-ahead, scrolling a preview into the action table, hidden
text, offscreen prose, semantic tables/ARIA lists/repeated articles/nested lists,
row/group/text limits, and marker versus action-guard behavior. The policy contract
also checks that list context stays read-only and uses only one decision request.
The DOM tests do not download a browser or call paid APIs; they skip if the cached
stealth binary or optional browser packages are unavailable.

A read-only check of **unfiltered** `https://careers.hitachi.com/jobs` found:

- 10 rendered rows; 2 visible and 8 below the viewport;
- five bounded previews at DOM positions 3–7, with job title, location, and company;
- no navigation/pagination group in list context.

[Raw output](evidence/list-context/hitachi-unfiltered.log) and the
[diagnostic harness](evidence/list-context/hitachi-harness.py.txt) are retained.
The live check navigates/observes only and uses no models. It does not claim a
Shanghai-filtered search, full-page enumeration, or a verified improvement in
model completion choices; those would require a separate end-to-end evaluation.

To repeat the read-only site check with the installed stealth extra:

```bash
cp docs/evidence/list-context/hitachi-harness.py.txt /tmp/jev-hitachi-list-check.py
PYTHONPATH=. uv run --env-file .env python /tmp/jev-hitachi-list-check.py
```

It uses a separate named daemon and CDP port 19344, then shuts down its owned browser
and daemon. Use an otherwise unused port. The normal offline checks are:

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```
