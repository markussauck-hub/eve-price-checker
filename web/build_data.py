#!/usr/bin/env python3
"""
Baut die Daten für die Website:

  items.json  – alle handelbaren Items (TypeID + Name), Quelle: EVE ESI
  build.json  – Bau-Rezepte dieser Items (Blueprint/Reaktion, Materialien), Quelle: Fuzzwork SDE

    python build_data.py                                   # schreibt ./items.json und ./build.json
    python build_data.py --out site/items.json --build-out site/build.json
    python build_data.py --local ./sde_csv                 # SDE-CSVs lokal statt Download

Nur Standardbibliothek.
"""

import argparse
import csv
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ESI = "https://esi.evetech.net/latest"
SDE_URL = "https://www.fuzzwork.co.uk/dump/latest/csv/"
USER_AGENT = "EVE-Price-Checker/1.0 (GitHub Pages build; github.com/markussauck-hub/eve-price-checker)"

ACT_MANUFACTURING = 1
ACT_REACTION = 11
SALVAGE_GROUP = 754      # invGroups: "Salvaged Materials"
PI_CATEGORY = 43         # invCategories: "Planetary Commodities"

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


# ---------------------------------------------------------------------------
# ESI: handelbare Items
# ---------------------------------------------------------------------------
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


def build_items():
    print("Lade /markets/prices/ …", flush=True)
    type_ids = sorted({p["type_id"] for p in request("GET", "/markets/prices/")})
    print(f"  {len(type_ids)} TypeIDs", flush=True)

    items = {}
    for i in range(0, len(type_ids), 1000):
        chunk = request("POST", "/universe/names/", type_ids[i:i + 1000])
        items.update({e["id"]: e["name"] for e in chunk if e.get("category") == "inventory_type"})
    print(f"  {len(items)} Namen aufgelöst", flush=True)
    return items


# ---------------------------------------------------------------------------
# Fuzzwork SDE: Bau-Rezepte
# ---------------------------------------------------------------------------
def rows(name, local_dir=None):
    if local_dir:
        text = (Path(local_dir) / f"{name}.csv").read_text(encoding="utf-8-sig")
    else:
        url = f"{SDE_URL}{name}.csv"
        print(f"Lade {url}", flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=300) as resp:
            text = resp.read().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = [h.strip().lower() for h in next(reader)]
    for row in reader:
        yield dict(zip(header, row))


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def build_recipes(items, local_dir=None):
    names, published, group_of = {}, set(), {}
    for r in rows("invTypes", local_dir):
        tid = to_int(r.get("typeid"))
        if tid is None:
            continue
        names[tid] = r.get("typename", "")
        gid = to_int(r.get("groupid"))
        if gid is not None:
            group_of[tid] = gid
        if str(r.get("published", "")).strip().lower() in ("1", "true"):
            published.add(tid)

    category_of = {}
    for r in rows("invGroups", local_dir):
        gid, cid = to_int(r.get("groupid")), to_int(r.get("categoryid"))
        if None not in (gid, cid):
            category_of[gid] = cid

    def collect(name, key_col, val_col):
        out = {}
        for r in rows(name, local_dir):
            bp, act = to_int(r.get("typeid")), to_int(r.get("activityid"))
            if act not in (ACT_MANUFACTURING, ACT_REACTION):
                continue
            k, v = to_int(r.get(key_col)), to_int(r.get(val_col))
            if None not in (bp, k, v):
                out.setdefault((bp, act), []).append([k, v])
        return out

    products = collect("industryActivityProducts", "producttypeid", "quantity")
    materials = collect("industryActivityMaterials", "materialtypeid", "quantity")

    # Produkt -> (Blueprint, Aktivität, Stück pro Run). Bau vor Reaktion, veröffentlichte BPs bevorzugt.
    def rank(bp, act):
        return (act == ACT_MANUFACTURING, bp in published)

    made_by = {}
    for (bp, act), prods in products.items():
        if not materials.get((bp, act)):
            continue
        for prod, qty in prods:
            known = made_by.get(prod)
            if known is None or rank(bp, act) > rank(known[0], known[1]):
                made_by[prod] = (bp, act, qty)

    recipes, used = {}, set()
    for prod in items:
        if prod not in made_by:
            continue
        bp, act, qty = made_by[prod]
        mats = sorted(materials[(bp, act)], key=lambda m: names.get(m[0], "").lower())
        recipes[str(prod)] = [bp, act, qty, mats]
        used.add(bp)
        used.update(m for m, _ in mats)

    tags = {}
    for i in used:
        gid = group_of.get(i)
        if gid == SALVAGE_GROUP:
            tags[str(i)] = "S"
        elif gid is not None and category_of.get(gid) == PI_CATEGORY:
            tags[str(i)] = "P"

    print(f"  {len(recipes)} baubare Items, {len(used)} Blueprints/Materialien", flush=True)
    return {
        "source": "Fuzzwork SDE dump (fuzzwork.co.uk/dump/latest/csv/)",
        "names": {str(i): names.get(i, f"#{i}") for i in sorted(used)},
        "tags": tags,
        "r": recipes,
    }


def write(path, data):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Geschrieben: {out} ({out.stat().st_size / 1024:.0f} KB)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="items.json")
    ap.add_argument("--build-out", default="build.json")
    ap.add_argument("--local", help="Ordner mit lokalen SDE-CSVs statt Download")
    args = ap.parse_args()

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    items = build_items()
    write(args.out, {
        "generated": generated,
        "items": sorted(([tid, name] for tid, name in items.items()), key=lambda x: x[1].lower()),
    })

    recipes = build_recipes(items, args.local)
    if not recipes["r"]:
        sys.exit("Keine Bau-Rezepte gefunden – Datenquelle prüfen.")
    write(args.build_out, {"generated": generated, **recipes})


if __name__ == "__main__":
    main()
