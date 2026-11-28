"""
Turn the site's Media Kit page into media-kit.pdf, run by the daily GitHub Action right after the stats update.

The "Download PDF" button on lilimagana.com/#media-kit hands out this file, so every device (iPhone included)
gets the same one-page, letter-size PDF with that morning's numbers.

Needs:  pip install playwright  &&  python -m playwright install --with-deps chromium
"""

import functools
import http.server
import os
import threading

from playwright.sync_api import sync_playwright

PORT = 8765
OUT = "media-kit.pdf"


def serve():
    # serve this folder (index.html, stats.json, photos/ ...) quietly on localhost
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(Quiet, directory=os.getcwd())
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


srv = serve()
with sync_playwright() as p:
    chrome = os.environ.get("CHROME_PATH")  # only for testing on a computer with Chrome installed
    browser = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    page.goto(f"http://127.0.0.1:{PORT}/index.html#media-kit", wait_until="networkidle")
    page.wait_for_function("document.getElementById('mkUpd').textContent.includes('last updated')", timeout=20000)
    page.wait_for_timeout(1500)  # photos and fonts settle
    page.emulate_media(media="print")
    page.pdf(path=OUT, prefer_css_page_size=True, print_background=True)
    browser.close()
srv.shutdown()
print(f"Saved {OUT} ({os.path.getsize(OUT) // 1024} KB)")
