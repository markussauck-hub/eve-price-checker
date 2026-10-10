"""Prüft die veröffentlichte Seite im echten Browser gegen die echte ESI-API.

    python web/tests/live_check.py https://markussauck-hub.github.io/eve-price-checker/
"""
import sys
from playwright.sync_api import sync_playwright

url = sys.argv[1].rstrip("/") + "/#34"  # Tritanium
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1366, "height": 860})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(url)
    pg.wait_for_function("document.querySelector('#sub').textContent.includes('Stand')", timeout=60000)
    hubs = [" ".join(c.inner_text().split()) for c in pg.query_selector_all(".hub")]
    routes = [" ".join(r.inner_text().split()) for r in pg.query_selector_all("#routes .row")]
    status = pg.text_content("#status")
    count = pg.text_content("#count")
    pg.screenshot(path="live.png", full_page=True)

    # Baumaterialien: Hammerhead II (baubar) und Tritanium (nicht baubar)
    trit_bp = pg.text_content("#bpline")
    pg.evaluate("location.hash = '#2185'")
    pg.wait_for_function("document.querySelector('#bpline').textContent.includes('Blueprint:')", timeout=60000)
    bpline = pg.text_content("#bpline")
    mats = [" ".join(r.inner_text().split()) for r in pg.query_selector_all("#mats tr")]
    copy_ok = not pg.is_disabled("#copy-mats")
    pg.screenshot(path="live_mats.png", full_page=True)
    b.close()

lines = [f"Items: {count}", *hubs, *routes, f"Status: {status}",
         f"Tritanium Bau: {trit_bp}", f"Hammerhead II: {bpline}", *mats]
print("\n".join(lines))
ok = (not errors and "Fehler" not in status and all("ISK" in h for h in hubs[:1])
      and "Nicht baubar" in trit_bp and len(mats) >= 3 and copy_ok)
msg = "%0A".join(lines + ([f"JS-Fehler: {errors}"] if errors else []))
print(f"::{'notice' if ok else 'error'} title=Live-Test::{msg}")
sys.exit(0 if ok else 1)
