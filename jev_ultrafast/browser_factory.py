"""Select the fork's browser backend without coupling the core executor to it."""

import os

from .browser import Browser


def create_browser(url):
    """Create the configured browser; this fork defaults to optional stealth mode."""
    if os.environ.get("JEV_BROWSER", "stealth").lower() == "chrome":
        return Browser(url)

    from .stealth import create_stealth_browser

    return create_stealth_browser(url)
