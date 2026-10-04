"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
NAVIGATION_RESPONSE_TIMEOUT = 30
OBSERVATION_SETTLE_TIMEOUT = 30

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Browser:
    def __init__(self, url, input_dispatch=None, input_dispatch_factory=None):
        ensure_daemon()
        self.input_dispatch = input_dispatch
        self.input_dispatch_factory = input_dispatch_factory
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.targets = {self.target}
        try:
            self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
            self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
            # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
            self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            try:
                # Page.navigate normally acknowledges immediately, but DNS and proxy
                # negotiation can delay the CDP response beyond Browser Harness's
                # five-second default. This is one mutation with a longer transport
                # budget, never a retry.
                self.call("Page.navigate", url=url, _response_timeout=NAVIGATION_RESPONSE_TIMEOUT)
            except TimeoutError as exc:
                raise TimeoutError(
                    f"Initial navigation was not acknowledged within {NAVIGATION_RESPONSE_TIMEOUT} "
                    "seconds. Check the browser proxy and its loopback bypass settings."
                ) from exc
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.evaluate("document.readyState") == "complete":
                    break
                time.sleep(0.02)
        except Exception:
            # Constructor failures must not leak a target. Do not retry the
            # navigation: its outcome may be unknown after a transport timeout.
            try:
                cdp("Target.closeTarget", targetId=self.target)
            except Exception:
                pass
            self.target = None
            raise

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def _stop_input_dispatch(self):
        input_dispatch = getattr(self, "input_dispatch", None)
        if input_dispatch is not None and hasattr(input_dispatch, "stop"):
            input_dispatch.stop()
        self.input_dispatch = None

    def _adopt_owned_popup(self):
        """Attach to a new page opened by this browser's exact target chain."""
        infos = cdp("Target.getTargets").get("targetInfos", [])
        owned = getattr(self, "targets", {self.target})
        candidates = [
            info
            for info in infos
            if info.get("type") == "page"
            and info.get("targetId") not in owned
            and info.get("openerId") in owned
        ]
        if not candidates:
            return False
        # Direct children of the current page are the least ambiguous when a
        # site happens to open more than one owned page at once.
        info = next((item for item in candidates if item.get("openerId") == self.target), candidates[0])
        target = info["targetId"]
        session = cdp("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        self._stop_input_dispatch()
        self.targets.add(target)
        self.target = target
        self.session = session
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        return True

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            adopted = action["kind"] in {"click", "submit"} and self._adopt_owned_popup()
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
            if action["kind"] in {"click", "submit"} and not adopted:
                self._adopt_owned_popup()
        # Navigation can take seconds, especially with stealth/proxy browsing.
        # Retry only this read: input and its execution log must never be replayed.
        deadline = time.monotonic() + OBSERVATION_SETTLE_TIMEOUT
        delay = 0.02
        while True:
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot}
                )
            except StalePage:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.5)

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "fill", "select", "submit"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if self.input_dispatch is None and self.input_dispatch_factory is not None:
            # Attach lazily so read-only runs avoid a second input client. The
            # factory receives only the code-owned CDP target ID.
            self.input_dispatch = self.input_dispatch_factory(self.target)
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        result = browser_operation(
            {"operation": "act", "session": self.session, "action": action, "text": text, "input": self.input_dispatch}
        )
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def close(self):
        self._stop_input_dispatch()
        targets = list(getattr(self, "targets", {self.target} if self.target else set()))
        for target in reversed(targets):
            try:
                cdp("Target.closeTarget", targetId=target)
            except Exception:
                pass
        self.targets = set()
        self.target = None


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        input_dispatch = request.get("input")
        if kind == "scroll":
            if input_dispatch is not None:
                input_dispatch.wheel(action["delta"])
            else:
                call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            def resolve_target():
                return evaluate("""(action => {
                  const e=window.__jevFast?.nodes.get(action.node);
                  if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                      !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
                  if (['fill','submit'].includes(action.kind) &&
                      (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
                  if (action.kind==='submit' &&
                      (e.tagName!=='INPUT' || !String(e.value).trim() || String(e.value)!==action.value)) return null;
                  const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
                  if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
                  if (!e.contains(document.elementFromPoint(x,y))) return null;
                  if (action.kind==='select') {
                    if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                        !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                    e.value=action.value;
                    e.dispatchEvent(new Event('input',{bubbles:true}));
                    e.dispatchEvent(new Event('change',{bubbles:true}));
                  }
                  return {x,y};
                })(""" + json.dumps(action) + ")")

            target = resolve_target()
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                if input_dispatch is not None:
                    # Moving a real pointer can itself trigger hover layout. Recheck the
                    # observed node before mouse-down so the curve cannot land elsewhere.
                    input_dispatch.move(x, y)
                    if resolve_target() != target:
                        raise StalePage("Target moved or became covered during pointer movement. Observe again.")
                    input_dispatch.click()
                    if kind == "fill":
                        input_dispatch.select_all()
                        input_dispatch.type_text(request["text"])
                    elif kind == "submit":
                        input_dispatch.press_key("Enter")
                else:
                    for event in ("mousePressed", "mouseReleased"):
                        call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                    if kind == "fill":
                        call(
                            "Input.dispatchKeyEvent",
                            type="keyDown",
                            key="a",
                            code="KeyA",
                            modifiers=4 if sys.platform == "darwin" else 2,
                            commands=["selectAll"],
                        )
                        call(
                            "Input.dispatchKeyEvent",
                            type="keyUp",
                            key="a",
                            code="KeyA",
                            modifiers=4 if sys.platform == "darwin" else 2,
                        )
                        call("Input.insertText", text=request["text"])
                    elif kind == "submit":
                        key = {
                            "key": "Enter",
                            "code": "Enter",
                            "windowsVirtualKeyCode": 13,
                            "nativeVirtualKeyCode": 13,
                        }
                        call("Input.dispatchKeyEvent", type="keyDown", text="\r", **key)
                        call("Input.dispatchKeyEvent", type="keyUp", **key)
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
