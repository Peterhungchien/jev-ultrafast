"""Local DOM regressions. Use only an already cached binary; never download or call models."""

from pathlib import Path

import pytest

from jev_ultrafast.browser import READ_STATE


@pytest.fixture(scope="module")
def chromium():
    cloakbrowser = pytest.importorskip("cloakbrowser")
    playwright = pytest.importorskip("playwright.sync_api")
    info = cloakbrowser.binary_info()
    if not info["installed"] or not Path(info["binary_path"]).is_file():
        pytest.skip("Local DOM checks require an already cached CloakBrowser binary")
    with playwright.sync_playwright() as client:
        browser = client.chromium.launch(
            executable_path=info["binary_path"], headless=True,
            args=["--disable-frame-rate-limit", "--disable-background-timer-throttling"],
        )
        yield browser
        browser.close()


@pytest.fixture
def dom(chromium):
    context = chromium.new_context(viewport={"width": 1120, "height": 780})
    page = context.new_page()
    page.set_default_timeout(5000)
    session = context.new_cdp_session(page)
    session.send("Emulation.setDeviceMetricsOverride", {
        "width": 1120, "height": 780, "deviceScaleFactor": 1, "mobile": False,
    })
    yield page
    context.close()


def listings(count=10):
    return (
        '<style>body,ul{margin:0;padding:0}li{display:block;height:400px}</style>'
        '<ul aria-label="Jobs">'
        + "".join(f'<li><a href="#job-{i}">Job {i}</a><p>Location {i}</p></li>' for i in range(1, count + 1))
        + "</ul>"
    )


def observe(dom, html):
    # No resources or scripts: write synchronously rather than waiting for
    # Playwright's lifecycle console marker on a stealth browser build.
    dom.evaluate("html => { document.open(); document.write(html); document.close(); }", html)
    return dom.evaluate(READ_STATE)


def test_list_lookahead_has_counts_but_never_offscreen_action_targets(dom):
    state = observe(dom, listings())
    group, = state["list_context"]["groups"]
    assert group["label"] == "Jobs"
    assert group["rendered_rows"] == 10
    assert group["visible_rows"] == 2
    assert group["rows_below_viewport"] == 8
    assert group["reported_total"] is None
    assert [row["position"] for row in group["preview"]] == [3, 4]
    assert group["preview"][0]["text"] == "Job 3 Location 3"
    assert "Job 3" not in state["text"]
    assert "Job 5" not in str(state["list_context"])
    assert {a["label"] for a in state["actions"] if a.get("role") == "link"} == {"Job 1", "Job 2"}
    assert all(set(row) == {"position", "text"} for row in group["preview"])
    assert any(a["id"] == "scroll_down" for a in state["actions"])


def test_scrolling_promotes_preview_row_to_observed_target(dom):
    observe(dom, listings())
    dom.evaluate("scrollTo(0, 800)")
    state = dom.evaluate(READ_STATE)
    links = {a["label"] for a in state["actions"] if a.get("role") == "link"}
    assert "Job 3" in links and "Job 1" not in links
    assert [row["position"] for row in state["list_context"]["groups"][0]["preview"]] == [5, 6]


def test_preview_churn_changes_marker_but_not_an_unrelated_action_guard(dom):
    state = observe(dom, listings())
    target = next(a for a in state["actions"] if a["label"] == "Job 1")
    guard = state["guards"][str(target["node"])]
    dom.evaluate("document.querySelectorAll('li')[2].querySelector('a').textContent='Updated job'")
    changed = dom.evaluate(READ_STATE)
    assert changed["marker"] != state["marker"]
    assert changed["guards"][str(target["node"])] == guard
    assert changed["list_context"]["groups"][0]["preview"][0]["text"].startswith("Updated job")


def test_hidden_content_navigation_and_offscreen_prose_are_not_previewed(dom):
    html = listings().replace("<p>Location 3</p>", '<p>Location 3</p><span aria-hidden="true">SECRET</span>')
    html += '<footer><ul><li>Footer one</li><li>Footer two</li></ul></footer>'
    html += '<nav><ul><li>Nav one</li><li>Nav two</li></ul></nav>'
    html += '<article><p>Unrelated offscreen article body</p></article>'
    state = observe(dom, html)
    assert len(state["list_context"]["groups"]) == 1
    context = str(state["list_context"])
    assert "SECRET" not in context and "Footer" not in context and "Nav" not in context
    assert "Unrelated offscreen article" not in context


@pytest.mark.parametrize("kind", ["table", "aria", "articles", "nested"])
def test_common_semantic_lists_are_grouped_without_duplicate_rows(dom, kind):
    if kind == "table":
        html = '<article><table aria-rowcount="100"><caption>Results</caption><tbody>'
        html += "".join(f'<tr style="height:400px"><td>Row {i}</td></tr>' for i in range(5))
        html += "</tbody></table></article>"
    elif kind == "aria":
        html = '<div role="list" aria-label="Results">'
        html += "".join(f'<div role="listitem" aria-setsize="100" style="height:400px">Row {i}</div>'
                        for i in range(5))
        html += "</div>"
    elif kind == "articles":
        html = '<main><h2>Results</h2><section>'
        html += "".join(f'<article style="height:400px">Row {i}</article>' for i in range(5))
        html += "</section></main>"
    else:
        html = '<ul aria-label="Results"><li style="height:400px">One<ul><li>A</li><li>B</li></ul></li>'
        html += '<li style="height:400px">Two</li><li style="height:400px">Three</li></ul>'
    state = observe(dom, html)
    groups = state["list_context"]["groups"]
    if kind == "nested":
        assert [g["rendered_rows"] for g in groups] == [3, 2]
    else:
        assert len(groups) == 1
        assert groups[0]["rendered_rows"] == 5
        if kind in {"table", "aria"}:
            assert groups[0]["reported_total"] == 100
        if kind == "table":
            assert groups[0]["label"] == "Results"


def test_wholly_offscreen_lists_and_css_hidden_rows_are_excluded(dom):
    html = '<div style="height:1700px">Visible prose</div>' + listings()
    state = observe(dom, html)
    assert state["list_context"]["groups"] == []
    html = listings().replace('<li><a href="#job-3">', '<li style="display:none"><a href="#job-3">')
    state = observe(dom, html)
    group, = state["list_context"]["groups"]
    assert group["rendered_rows"] == 9
    assert "Job 3" not in str(group["preview"])


def test_row_scan_is_bounded_and_does_not_claim_uninspected_rows(dom):
    state = observe(dom, listings(300))
    group, = state["list_context"]["groups"]
    assert group["rendered_rows"] == 200
    assert group["uninspected_rows"] == 100


def test_preview_group_row_and_text_budgets(dom):
    html = '<style>ul{position:absolute;top:0;margin:0}li{height:100px}</style>'
    for _ in range(6):
        html += '<ul>' + ''.join(f'<li>{"x" * 300} {i}</li>' for i in range(30)) + '</ul>'
    state = observe(dom, html)
    groups = state["list_context"]["groups"]
    assert len(groups) == 4
    assert state["list_context"]["omitted_groups"] == 2
    assert all(len(group["preview"]) <= 6 for group in groups)
    assert all(len(row["text"]) <= 240 for group in groups for row in group["preview"])
    assert sum(len(row["text"]) for group in groups for row in group["preview"]) <= 2400
    assert any(group["omitted_preview_rows"] > 0 for group in groups)
