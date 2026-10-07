"""Live-Test gegen ESI: Preise für Tritanium an allen Hubs + Routenberechnung."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import eve_price_checker as m

TYPE_ID = 34  # Tritanium

r = m.SESSION.post(f"{m.ESI}/universe/names/", json=[TYPE_ID], timeout=20)
r.raise_for_status()
assert r.json()[0]["name"] == "Tritanium", r.json()

results = {st: m.safe_fetch(TYPE_ID, st) for st in m.STATIONS}
for st, res in results.items():
    print(f"{st:12} buy={m.fmt_isk(res['buy']):>14}  sell={m.fmt_isk(res['sell']):>14}  error={res['error']}")
    assert res["error"] is None, f"{st}: {res['error']}"

jita = results["Jita 4-4"]
assert jita["buy"] and jita["sell"] and jita["buy"] <= jita["sell"], jita

routes = sorted(
    (m.calc_route(results[s]["sell"], results[d]["buy"]) + (s, d)
     for s in results for d in results if s != d and m.calc_route(results[s]["sell"], results[d]["buy"])),
    reverse=True,
)
for profit, pct, s, d in routes[: m.ROUTES_SHOWN]:
    print(f"Route {s} -> {d}: {profit:+,.2f} ISK ({pct:+.1f} %)")
print("OK")
