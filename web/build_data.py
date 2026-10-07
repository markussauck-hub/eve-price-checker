#!/usr/bin/env python3
"""
Baut items.json (alle handelbaren Items: TypeID + Name) für die Website.

    python build_data.py                     # schreibt ./items.json
    python build_data.py --out site/items.json

Quelle: EVE ESI (/markets/prices/ + /universe/names/) – gleiche Logik wie die Desktop-Version.
Nur Standardbibliothek.
"""

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ESI = "https://esi.evetech.net/latest"
USER_AGENT = "EVE-Price-Checker/1.0 (GitHub Pages build; github.com/markussauck-hub/eve-price-checker)"


def request(method, path, body=None, tries=4):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(f"{ESI}{path}", data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except Exception as e:  # noqa: BLE001 – ESI wackelt gelegentlich (502/503/504)
            if attempt == tries:
                raise
            print(f"  {method} {path} fehlgeschlagen ({e}), neuer Versuch …", flush=True)
            time.sleep(2 * attempt)


def build():
    print("Lade /markets/prices/ …", flush=True)
    type_ids = sorted({p["type_id"] for p in request("GET", "/markets/prices/")})
    print(f"  {len(type_ids)} TypeIDs", flush=True)

    items = {}
    for i in range(0, len(type_ids), 1000):
        chunk = request("POST", "/universe/names/", type_ids[i:i + 1000])
        items.update({e["id"]: e["name"] for e in chunk if e.get("category") == "inventory_type"})
    print(f"  {len(items)} Namen aufgelöst", flush=True)

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": sorted(([tid, name] for tid, name in items.items()), key=lambda x: x[1].lower()),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="items.json")
    args = ap.parse_args()

    data = build()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Geschrieben: {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
