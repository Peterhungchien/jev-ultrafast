"""CloakBrowser stealth session: this fork's default browser.

One stealth Chromium launches per process (headed unless JEV_HEADLESS is set).
browser-harness is bound to it through BU_CDP_URL, restarted first if a daemon
from an earlier local-Chrome session is still alive. Input dispatches through
a humanized Playwright client that owns its own worker thread — cloakbrowser's
sync launch leaves an asyncio loop running in the main thread, which forbids
the Playwright Sync API there.

Set JEV_BROWSER=chrome to fall back to the local Chrome path with instant CDP
input. JEV_HEADLESS=1 runs the stealth browser headless; JEV_HUMANIZE=0 turns
off behavioral humanization; JEV_CDP_PORT overrides the DevTools port.
"""

import atexit
import os
import threading
import time

_LOCK = threading.Lock()
_BROWSER = None
_STEALTH_INSTALL = (
    "Stealth browsing needs the optional dependencies. Run `uv sync --extra stealth` "
    "or install `jev-ultrafast[stealth]`."
)


def cdp_port():
    return int(os.environ.get("JEV_CDP_PORT", "9242"))


def cdp_url():
    return f"http://127.0.0.1:{cdp_port()}"


def _env_flag(name, default=True):
    return os.environ.get(name, "1" if default else "0").lower() not in {"0", "false", "no"}


def humanize_enabled():
    return _env_flag("JEV_HUMANIZE", default=True)


def ensure_stealth_browser():
    """Launch the stealth browser once per process and bind browser-harness to it.

    Must run before ensure_daemon(): BU_CDP_URL has to be in the environment
    when the daemon respawns, and a daemon left over from a local-Chrome
    session has to be restarted so it adopts the stealth browser.
    """
    global _BROWSER
    with _LOCK:
        os.environ["BU_CDP_URL"] = cdp_url()
        from browser_harness import admin

        if admin.daemon_alive() and admin.daemon_browser_kind() != "cdp":
            admin.restart_daemon()
        if _BROWSER is None:
            try:
                from cloakbrowser import launch
            except ModuleNotFoundError:
                raise RuntimeError(_STEALTH_INSTALL) from None

            _BROWSER = launch(
                headless=_env_flag("JEV_HEADLESS", default=False),
                humanize=humanize_enabled(),
                **(
                    {"proxy": os.environ["JEV_PROXY"]}
                    if os.environ.get("JEV_PROXY")
                    else {}
                ),
                **({"geoip": True} if os.environ.get("JEV_GEOIP", "").lower() in {"1", "true", "yes"} else {}),
                args=[
                    f"--remote-debugging-port={cdp_port()}",
                    "--remote-debugging-address=127.0.0.1",
                ],
            )
            atexit.register(_shutdown)
    return _BROWSER


def create_stealth_browser(url):
    """Launch the optional backend, then return the generic guarded executor."""
    from .browser import Browser

    ensure_stealth_browser()
    return Browser(
        url,
        input_dispatch_factory=lambda target_id: HumanInput(
            cdp_url(), target_id, humanize=humanize_enabled()
        ),
    )


def _shutdown():
    global _BROWSER
    if _BROWSER is not None:
        try:
            _BROWSER.close()
        except Exception:
            pass
        _BROWSER = None


class HumanInput:
    """jev input primitives dispatched through a humanized Playwright page.

    The humanized client lives in a dedicated worker thread (no asyncio loop
    there), because the stealth launch leaves an asyncio loop running in the
    main thread, which forbids the Playwright Sync API. Calls cross via a
    command queue; each result comes back over a per-call result queue.
    """

    def __init__(self, cdp_url, target_id, *, humanize=True, timeout=180):
        import queue

        self._timeout = timeout
        self._commands = queue.Queue()
        self._ready = threading.Event()
        self._error = None
        self._worker = threading.Thread(target=self._serve, args=(cdp_url, target_id, humanize), daemon=True)
        self._worker.start()
        self._ready.wait(60)
        if self._error:
            raise self._error

    def _serve(self, cdp_url, target_id, humanize):
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            browser = self._pw.chromium.connect_over_cdp(cdp_url)
            if humanize:
                from cloakbrowser.human import patch_browser, resolve_config

                patch_browser(browser, resolve_config("default"))
            self._page = self._find_target_page(browser, target_id)
            self._browser = browser
            self._ready.set()
            while True:
                command = self._commands.get()
                if command is None:
                    break
                name, args, result = command
                try:
                    result.put((True, getattr(self, "_do_" + name)(*args)))
                except Exception as error:  # report the real failure to the caller
                    result.put((False, error))
            browser.close()
            self._pw.stop()
        except Exception as error:
            self._error = error
            self._ready.set()

    @staticmethod
    def _find_target_page(browser, target_id):
        deadline = time.time() + 15
        while time.time() < deadline:
            for context in browser.contexts:
                for page in context.pages:
                    session = None
                    try:
                        session = context.new_cdp_session(page)
                        info = session.send("Target.getTargetInfo")["targetInfo"]
                        if info["targetId"] == target_id:
                            return page
                    except Exception:
                        continue
                    finally:
                        if session is not None:
                            try:
                                session.detach()
                            except Exception:
                                pass
            time.sleep(0.2)
        raise RuntimeError(f"Could not find jev target {target_id!r} over CDP.")

    def _call(self, name, *args):
        import queue

        result = queue.Queue()
        self._commands.put((name, args, result))
        ok, value = result.get(timeout=self._timeout)
        if not ok:
            raise RuntimeError(f"Humanized input '{name}' failed") from value
        return value

    def _do_move(self, x, y):
        self._page.mouse.move(x, y)

    def _do_click(self):
        raw_mouse = getattr(self._page, "_human_raw_mouse", None)
        config = getattr(self._page, "_human_cfg", None)
        if raw_mouse is not None and config is not None:
            from cloakbrowser.human import human_click

            human_click(raw_mouse, False, config)
        else:
            self._page.mouse.down()
            self._page.mouse.up()

    def _do_select_all(self):
        self._page.keyboard.press("ControlOrMeta+a")

    def _do_type_text(self, text):
        self._page.keyboard.type(text)

    def _do_wheel(self, delta):
        self._page.mouse.wheel(0, delta)

    def move(self, x, y):
        self._call("move", x, y)

    def click(self):
        self._call("click")

    def select_all(self):
        self._call("select_all")

    def type_text(self, text):
        self._call("type_text", text)

    def wheel(self, delta):
        self._call("wheel", delta)

    def stop(self):
        self._commands.put(None)
        self._worker.join(timeout=10)
