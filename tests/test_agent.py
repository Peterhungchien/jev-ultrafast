"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import os
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_submit_has_its_own_operation_head_on_the_same_element():
    actions = [*page()["actions"]]
    actions.insert(2, {
        "id": "submit",
        "kind": "submit",
        "label": "Submit Search",
        "role": "textbox",
        "value": "query",
        "node": 10,
    })

    elements, targets, _controls = model.action_space(actions)

    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK", "SUBMIT"]
    assert targets["SUBMIT"]["1"]["id"] == "submit"


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_invalid_text_output_uses_configured_fallback_model(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL", "small")
    monkeypatch.setenv("TEXT_MODEL_FALLBACK", "strong")
    post = Mock(side_effect=[
        {"choices": [{"message": {"content": '{"text":null}'}}], "usage": {"total_tokens": 8}},
        {"choices": [{"message": {"content": '{"text":null}'}}], "usage": {"total_tokens": 10}},
        {"choices": [{"message": {"content": '{"text":"竖笛小魔王"}'}}], "usage": {"total_tokens": 12}},
    ])
    monkeypatch.setattr(model, "post_json", post)

    value, helper = model.field_text({"goal": "Search for the creator"})

    assert value == "竖笛小魔王"
    assert [call.args[2]["model"] for call in post.call_args_list] == ["small", "strong", "strong"]
    assert helper["model"] == "strong" and helper["calls"] == 3
    assert [attempt["usage"]["total_tokens"] for attempt in helper["attempts"]] == [8, 10, 12]
    assert all(call.args[2]["temperature"] == 0 for call in post.call_args_list)


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


@pytest.mark.parametrize("settle_after", [0, 3, None])
def test_observation_retries_reads_until_settled_or_deadline(monkeypatch, settle_after):
    import jev_ultrafast.browser as browser

    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    expected = page()
    calls = []

    def observe(request):
        calls.append(request)
        if settle_after is None or now[0] < settle_after:
            raise StalePage("Document is navigating")
        return expected

    monkeypatch.setattr(browser.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(browser.time, "sleep", sleep)
    monkeypatch.setattr(browser, "browser_operation", observe)
    owned = browser.Browser.__new__(browser.Browser)
    owned.session = "test"
    if settle_after is None:
        with pytest.raises(StalePage, match="Document is navigating"):
            owned.observe(screenshot=False)
        assert now[0] == browser.OBSERVATION_SETTLE_TIMEOUT
    else:
        assert owned.observe(screenshot=False) is expected
        assert settle_after <= now[0] < settle_after + 0.5
    assert all(call == {"operation": "observe", "session": "test", "screenshot": False} for call in calls)
    assert all(0 < delay <= 0.5 for delay in sleeps)
    if settle_after == 0:
        assert len(calls) == 1 and not sleeps


def test_slow_post_action_navigation_does_not_replay_mutation(runner, monkeypatch):
    import jev_ultrafast.browser as browser

    owned = browser.Browser.__new__(browser.Browser)
    owned.session = "test"
    now = [0.0]
    monkeypatch.setattr(browser.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(browser.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay))
    expected = page()

    def observe(request):
        assert len(runner.state["history"]) == 1  # Execution logged before every read.
        if now[0] < 3:
            raise StalePage("Document is navigating")
        return expected

    monkeypatch.setattr(browser, "browser_operation", observe)
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision("e3")))
    runner.state["browser"].observe.side_effect = owned.observe
    state = runner.command("tick")
    assert state["status"] == "ready"
    assert len(state["history"]) == 1
    runner.state["browser"].act.assert_called_once()
    loop.choose.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_initial_navigation_uses_a_proxy_tolerant_response_budget(monkeypatch):
    import jev_ultrafast.browser as browser

    calls = []

    def cdp(method, session_id=None, **params):
        calls.append((method, session_id, params))
        if method == "Target.createTarget":
            return {"targetId": "target-1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        if method == "Runtime.evaluate":
            return {"result": {"value": "complete"}}
        return {}

    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", cdp)

    owned = browser.Browser("https://example.test")
    owned.close()

    navigation = next(call for call in calls if call[0] == "Page.navigate")
    assert navigation[2]["_response_timeout"] == browser.NAVIGATION_RESPONSE_TIMEOUT == 30


def test_initial_navigation_timeout_is_not_retried_and_closes_target(monkeypatch):
    import jev_ultrafast.browser as browser

    calls = []

    def cdp(method, session_id=None, **params):
        calls.append((method, session_id, params))
        if method == "Target.createTarget":
            return {"targetId": "target-1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        if method == "Page.navigate":
            raise TimeoutError("slow proxy")
        return {}

    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", cdp)

    with pytest.raises(TimeoutError, match="not acknowledged within 30 seconds"):
        browser.Browser("https://example.test")

    assert sum(call[0] == "Page.navigate" for call in calls) == 1
    assert [call for call in calls if call[0] == "Target.closeTarget"] == [
        ("Target.closeTarget", None, {"targetId": "target-1"})
    ]


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.input_dispatch = None
    b.input_dispatch_factory = None
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


def test_target_scoped_freshness_ignores_unrelated_page_marker_churn():
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.evaluate = Mock(return_value=[["document", "https://example.test"], ["target-guard"]])
    observed = {
        "page_key": ["document", "https://example.test"],
        "guards": {"10": ["target-guard"]},
        "marker": ["old carousel"],
    }

    assert b.fresh(observed, {"kind": "fill", "node": 10})
    assert "c.guard" in b.evaluate.call_args.args[0]


def test_relevant_change_ignores_carousel_but_tracks_target_scope():
    before = {
        "fingerprint": "old-carousel",
        "page_key": ["document", "https://example.test", 0],
        "guards": {"10": ["search", "query"]},
    }
    carousel = {
        "fingerprint": "new-carousel",
        "page_key": ["document", "https://example.test", 0],
        "guards": {"10": ["search", "query"]},
    }
    target = deepcopy(carousel)
    target["guards"]["10"] = ["search", "submitted"]
    action = {"kind": "submit", "node": 10}

    assert loop.relevant_change(before, carousel, action) is False
    assert loop.relevant_change(before, target, action) is True


def test_humanized_pointer_rechecks_target_before_mouse_down(monkeypatch):
    import jev_ultrafast.browser as browser

    evaluations = iter([{"x": 50, "y": 25}, None])

    def cdp(method, **_params):
        assert method == "Runtime.evaluate"
        return {"result": {"value": next(evaluations)}}

    input_dispatch = Mock()
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(StalePage, match="pointer movement"):
        browser_operation({
            "operation": "act",
            "session": "test",
            "action": {"id": "e1", "kind": "click", "node": 1},
            "input": input_dispatch,
        })
    input_dispatch.move.assert_called_once_with(50, 25)
    input_dispatch.click.assert_not_called()


def test_submit_focuses_observed_field_and_presses_enter(monkeypatch):
    import jev_ultrafast.browser as browser

    calls = []

    def cdp(method, **params):
        calls.append((method, params))
        if method == "Runtime.evaluate":
            return {"result": {"value": {"x": 50, "y": 25}}}
        return {}

    monkeypatch.setattr(browser, "cdp", cdp)
    result = browser_operation({
        "operation": "act",
        "session": "test",
        "action": {"id": "e1", "kind": "submit", "node": 1, "value": "query"},
    })

    assert result == {"executed": "e1"}
    key_events = [params for method, params in calls if method == "Input.dispatchKeyEvent"]
    assert [(event["type"], event["key"]) for event in key_events] == [
        ("keyDown", "Enter"),
        ("keyUp", "Enter"),
    ]
    assert key_events[0]["text"] == "\r"


def test_humanized_submit_uses_target_bound_input_worker(monkeypatch):
    import jev_ultrafast.browser as browser

    input_dispatch = Mock()
    monkeypatch.setattr(
        browser,
        "cdp",
        Mock(return_value={"result": {"value": {"x": 50, "y": 25}}}),
    )

    browser_operation({
        "operation": "act",
        "session": "test",
        "action": {"id": "e1", "kind": "submit", "node": 1, "value": "query"},
        "input": input_dispatch,
    })

    input_dispatch.move.assert_called_once_with(50, 25)
    input_dispatch.click.assert_called_once_with()
    input_dispatch.press_key.assert_called_once_with("Enter")


def test_browser_lazily_builds_input_dispatch_for_exact_target(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.input_dispatch = None
    b.target = "target-7"
    b.session = "session-7"
    b.fresh = Mock(return_value=True)
    worker = Mock()
    b.input_dispatch_factory = Mock(return_value=worker)
    operation = Mock(return_value={"executed": "e1"})
    monkeypatch.setattr(browser, "browser_operation", operation)

    action = {"id": "e1", "kind": "click", "node": 1}
    b.act(action, page())

    b.input_dispatch_factory.assert_called_once_with("target-7")
    operation.assert_called_once_with({
        "operation": "act",
        "session": "session-7",
        "action": action,
        "text": None,
        "input": worker,
    })


def test_browser_adopts_only_a_popup_from_its_owned_target(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.target = "parent"
    b.targets = {"parent"}
    b.session = "old-session"
    worker = Mock()
    b.input_dispatch = worker
    calls = []

    def cdp(method, session_id=None, **params):
        calls.append((method, session_id, params))
        if method == "Target.getTargets":
            return {"targetInfos": [
                {"targetId": "foreign", "type": "page", "openerId": "someone-else"},
                {"targetId": "child", "type": "page", "openerId": "parent", "url": ""},
            ]}
        if method == "Target.attachToTarget":
            return {"sessionId": "child-session"}
        return {}

    monkeypatch.setattr(browser, "cdp", cdp)

    assert b._adopt_owned_popup() is True
    assert b.input_dispatch is None
    worker.stop.assert_called_once_with()
    assert b.target == "child" and b.session == "child-session"
    assert b.targets == {"parent", "child"}
    assert not any(call[2].get("targetId") == "foreign" for call in calls)


def test_browser_factory_keeps_direct_chrome_independent_of_stealth(monkeypatch):
    import jev_ultrafast.browser_factory as factory

    browser = Mock()
    monkeypatch.setenv("JEV_BROWSER", "chrome")
    monkeypatch.setattr(factory, "Browser", browser)

    factory.create_browser("https://example.test")

    browser.assert_called_once_with("https://example.test")


def test_browser_factory_loads_stealth_only_when_selected(monkeypatch):
    import jev_ultrafast.browser_factory as factory
    import jev_ultrafast.stealth as stealth

    create_stealth = Mock()
    monkeypatch.setenv("JEV_BROWSER", "stealth")
    monkeypatch.setattr(stealth, "create_stealth_browser", create_stealth)

    factory.create_browser("https://example.test")

    create_stealth.assert_called_once_with("https://example.test")


def test_stealth_inherits_standard_proxy_with_explicit_override(monkeypatch):
    import jev_ultrafast.stealth as stealth

    for name in (
        "JEV_PROXY",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://standard-proxy.test:8080")
    assert stealth.browser_proxy() == "http://standard-proxy.test:8080"

    monkeypatch.setenv("JEV_PROXY", "socks5://explicit-proxy.test:1080")
    assert stealth.browser_proxy() == "socks5://explicit-proxy.test:1080"

    monkeypatch.setenv("JEV_PROXY", "")
    assert stealth.browser_proxy() is None


def test_stealth_keeps_local_control_traffic_out_of_proxy(monkeypatch):
    import jev_ultrafast.stealth as stealth

    monkeypatch.setenv("NO_PROXY", "example.test")
    monkeypatch.setenv("no_proxy", "")
    stealth.ensure_loopback_proxy_bypass()

    for name in ("NO_PROXY", "no_proxy"):
        bypass = os.environ[name].split(",")
        assert all(host in bypass for host in stealth._LOOPBACK_HOSTS)
    assert os.environ["NO_PROXY"].split(",")[0] == "example.test"


@pytest.mark.parametrize("headless", ["0", "1"])
def test_new_stealth_browser_replaces_an_unverified_cdp_daemon(monkeypatch, headless):
    import cloakbrowser
    from browser_harness import admin

    import jev_ultrafast.stealth as stealth

    previous_browser = stealth._BROWSER
    launched = Mock()
    monkeypatch.setattr(stealth, "_BROWSER", None)
    monkeypatch.setattr(stealth.atexit, "register", Mock())
    monkeypatch.setattr(cloakbrowser, "launch", Mock(return_value=launched))
    monkeypatch.setattr(admin, "daemon_alive", Mock(return_value=True))
    monkeypatch.setattr(admin, "daemon_browser_kind", Mock(return_value="cdp"))
    restart = Mock()
    monkeypatch.setattr(admin, "restart_daemon", restart)
    monkeypatch.setenv("JEV_PROXY", "")
    monkeypatch.setenv("JEV_HEADLESS", headless)
    monkeypatch.setenv("BU_CDP_URL", "http://old.test")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")

    try:
        assert stealth.ensure_stealth_browser() is launched
        restart.assert_called_once_with()
        assert os.environ["BU_CDP_URL"] == stealth.cdp_url()
        cloakbrowser.launch.assert_called_once()
        kwargs = cloakbrowser.launch.call_args.kwargs
        assert kwargs["headless"] is (headless == "1")
        assert kwargs["args"] == [
            f"--remote-debugging-port={stealth.cdp_port()}",
            "--remote-debugging-address=127.0.0.1",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            *(["--disable-frame-rate-limit"] if headless == "0" else []),
        ]
    finally:
        stealth._BROWSER = previous_browser


def test_human_input_finds_its_exact_cdp_target():
    from jev_ultrafast.stealth import HumanInput

    first, expected = object(), object()
    first_session = Mock(send=Mock(return_value={"targetInfo": {"targetId": "target-1"}}))
    expected_session = Mock(send=Mock(return_value={"targetInfo": {"targetId": "target-2"}}))
    context = Mock()
    context.pages = [first, expected]
    context.new_cdp_session.side_effect = lambda candidate: {
        first: first_session,
        expected: expected_session,
    }[candidate]
    browser = Mock(contexts=[context])

    assert HumanInput._find_target_page(browser, "target-2") is expected
    first_session.detach.assert_called_once_with()
    expected_session.detach.assert_called_once_with()


@pytest.mark.parametrize("humanize,patch_calls", [(False, 0), (True, 1)])
def test_human_input_worker_honors_humanize_flag(monkeypatch, humanize, patch_calls):
    import queue
    import threading

    import cloakbrowser.human
    import playwright.sync_api

    from jev_ultrafast.stealth import HumanInput

    page_object = object()
    browser = Mock()
    playwright_client = Mock()
    playwright_client.chromium.connect_over_cdp.return_value = browser
    manager = Mock()
    manager.start.return_value = playwright_client
    patch = Mock()
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", Mock(return_value=manager))
    monkeypatch.setattr(cloakbrowser.human, "patch_browser", patch)

    worker = HumanInput.__new__(HumanInput)
    worker._commands = queue.Queue()
    worker._commands.put(None)
    worker._ready = threading.Event()
    worker._error = None
    worker._find_target_page = Mock(return_value=page_object)
    worker._serve("http://cdp.test", "target-2", humanize)

    assert worker._page is page_object and worker._error is None
    assert patch.call_count == patch_calls


def test_parallel_worker_output_is_prefixed_and_keep_open_is_signaled():
    import io
    import threading

    from examples.parallel_processes import KEEP_OPEN_READY, stream_output

    ready = threading.Event()
    destination = io.StringIO()
    stream_output(2, io.StringIO(f"ready  Search\n{KEEP_OPEN_READY}\n"), destination, ready)

    assert destination.getvalue().splitlines() == [
        "[worker 2] ready  Search",
        "[worker 2] task complete; browser remains open",
    ]
    assert ready.is_set()


def test_parallel_worker_stderr_has_a_distinct_prefix():
    import io
    import threading

    from examples.parallel_processes import stream_output

    ready = threading.Event()
    destination = io.StringIO()
    stream_output(3, io.StringIO("model failed\n"), destination, ready, stderr=True)

    assert destination.getvalue() == "[worker 3 stderr] model failed\n"
    assert not ready.is_set()


def test_demo_reset_accepts_a_custom_url(monkeypatch):
    import jev_ultrafast.demo as demo

    agent = Mock()
    agent.state = {}
    agent.snapshot.return_value = {
        "page": None,
        "status": "ready",
        "history": [],
        "decision": None,
    }
    constructor = Mock(return_value=agent)
    monkeypatch.setattr(demo, "AGENT", None)
    monkeypatch.setattr(demo, "Agent", constructor)

    demo.command("reset", {
        "scenario": "flights",
        "url": "  https://example.test/custom?q=1  ",
        "goal": "Inspect the custom page.",
    })

    constructor.assert_called_once_with(
        "https://example.test/custom?q=1",
        "Inspect the custom page.",
        screenshots=True,
        record_dir=None,
    )
    assert agent.state == {
        "scenario": "flights",
        "start_url": "https://example.test/custom?q=1",
    }


def test_demo_reset_keeps_scenario_url_when_custom_url_is_omitted(monkeypatch):
    import jev_ultrafast.demo as demo

    agent = Mock(state={})
    agent.snapshot.return_value = {
        "page": None,
        "status": "ready",
        "history": [],
        "decision": None,
    }
    constructor = Mock(return_value=agent)
    monkeypatch.setattr(demo, "AGENT", None)
    monkeypatch.setattr(demo, "Agent", constructor)

    demo.command("reset", {"scenario": "research", "goal": "Read the article."})

    assert constructor.call_args.args[0] == f"{demo.ORIGIN}/fixture.html?scenario=research"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///tmp/private",
        "javascript:alert(1)",
        "https://user:secret@example.test/",
        "https://example.test:99999/",
    ],
)
def test_demo_rejects_unsafe_or_invalid_custom_urls(url):
    import jev_ultrafast.demo as demo

    with pytest.raises(ValueError):
        demo.resolve_demo_url(url, "flights")


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()
