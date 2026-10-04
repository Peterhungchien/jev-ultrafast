"""Run jev against a bot-checked site.

This fork defaults to the CloakBrowser stealth browser with humanized input,
so the plain Agent entry point is all you need — the library launches the
stealth browser, binds browser-harness to it, and attaches the humanized
input layer automatically (see README "Stealth browsing by default").

    uv run --env-file .env python examples/stealth_browser.py --url URL --goal 'A narrow goal'

Environment knobs: JEV_BROWSER=chrome (regular Chrome, instant input),
JEV_HEADLESS=1, JEV_HUMANIZE=0, JEV_PROXY=..., JEV_CDP_PORT=....
"""

import argparse
import os

from jev_ultrafast import Agent

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--goal", action="append", required=True, help="Repeat for an ordered list of goals.")
parser.add_argument("--raw-input", action="store_true", help="Use regular Chrome with instant CDP input.")
args = parser.parse_args()

if args.raw_input:
    os.environ["JEV_BROWSER"] = "chrome"

with Agent(args.url, args.goal) as agent:
    for state in agent.run():
        print(f"{state['elapsed_ms']:>5} ms  {len(state['history'])} actions  {state['status']}")
    print(state["page"]["url"])
