"""Recipe pages through a real crawl of a test server on 127.0.0.1.

The server runs in this process on a free port bound to 127.0.0.1 only.
The pages are the synthetic fixtures from test_recipe.py.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from scrapepy.config import ScrapeConfig
from scrapepy.scraper import run_scraper

from .test_recipe import BASIC, BASIC_PROSE, STORY, TIPS, ld, page

TWO = [
    {"@type": "Recipe", "name": "Banana Pancakes", "recipeYield": 2,
     "recipeIngredient": ["1 ripe banana", "2 eggs"],
     "recipeInstructions": ["Mash the banana.", "Beat in the eggs and fry small rounds."]},
    {"@type": "Recipe", "name": "Berry Sauce",
     "recipeIngredient": ["100 g frozen berries"],
     "recipeInstructions": "Warm the berries until soft."},
]
LISTING = {"@type": "ItemList", "itemListElement": [{"@type": "ListItem", "item": BASIC}]}

PAGES = {
    "/recipes/oat-bites": page(ld(BASIC), BASIC_PROSE),
    "/recipes/pancakes": page(ld(TWO), f"<p>{STORY}</p>", title="Pancake Morning"),
    "/recipes/broken": page(ld(raw="{oops"), f"<p>{STORY}</p><p>{TIPS}</p>", title="Broken"),
    "/recipes/listing": page(ld(LISTING), f"<p>{STORY}</p><p>{TIPS}</p>", title="Listing"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            body = page("", "".join(f'<p><a href="{p}">{p}</a></p>' for p in PAGES)
                        + f"<p>{STORY}</p>", title="Home")
        elif path in PAGES:
            body = PAGES[path]
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def read(path):
    text = Path(path).read_text(encoding="utf-8")
    end = text.index("\n---\n", 3)
    return yaml.safe_load(text[4:end]), text[end + 5:].removeprefix("\n")


def test_crawl_writes_recipe_blocks(server, tmp_path):
    config = ScrapeConfig(url=server + "/", output_root=str(tmp_path), wait=0,
                          concurrent=1, markdown_only=True, incremental=False)
    code = asyncio.run(run_scraper(config))
    assert code == 0
    files = {}
    for md in Path(config.markdown_dir).rglob("*.md"):
        front, body = read(md)
        files[front["url"]] = (front, body)

    front, body = files[server + "/recipes/oat-bites"]
    assert front["page_type"] == "recipe"
    assert front["servings"] == "16 bites"
    assert front["total_time"] == "1 hour 20 minutes"
    assert front["keywords"] == ["oats", "no bake", "lunchbox"]
    assert ("## Ingredients\n\n- 150 g rolled oats\n- 3 tbsp smooth peanut butter\n"
            "- 2 tbsp runny honey\n- 50 g raisins\n") in body
    assert ("## Method\n\n1. Tip the oats into a large bowl.\n"
            "2. Stir in the peanut butter and honey until sticky.\n"
            "3. Fold through the raisins, roll into 16 balls and chill for 1 hour.\n") in body
    assert "- Calories: 95 kcal\n- Fat: 4.1 g\n" in body
    assert body.count("rolled oats") == 1
    assert TIPS in body

    front, body = files[server + "/recipes/pancakes"]
    assert front["page_type"] == "recipe" and front["servings"] == "2"
    assert "## Banana Pancakes" in body and "## Berry Sauce" in body
    assert "### Ingredients\n\n- 1 ripe banana\n- 2 eggs\n" in body

    for name in ("broken", "listing"):
        front, body = files[server + f"/recipes/{name}"]
        assert front["page_type"] != "recipe"
        assert "## Recipe details" not in body
        assert "wet Saturday" in body

    summary = json.loads((Path(config.output_dir) / "run_summary.json").read_text())
    assert summary["status"] == "complete"
    # Only the template's nav link to /recipes is missing on this server
    assert [f["url"] for f in summary["failed"]] == [server + "/recipes"]
    assert len(summary["new_markdown"]) == 4
