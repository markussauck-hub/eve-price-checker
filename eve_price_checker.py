"""
EVE Price Checker – Jita 4-4 / Amarr VIII
Zeigt beste Buy-/Sell-Preise beider Hubs und die Arbitrage-Spanne dazwischen.

Abhängigkeit: pip install requests
"""

import json
import os
import queue
import threading
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox, ttk

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
def data_dir():
    """Ablageort für den Item-Cache (unter Windows %LOCALAPPDATA%)."""
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        path = os.path.join(os.environ["LOCALAPPDATA"], "EVEPriceChecker")
    else:
        base = sys.executable if getattr(sys, "frozen", False) else __file__
        path = os.path.dirname(os.path.abspath(base))
    os.makedirs(path, exist_ok=True)
    return path


CACHE_FILE = os.path.join(data_dir(), "eve_items_cache.json")
CACHE_MAX_AGE = 7 * 24 * 3600  # 1 Woche

ESI = "https://esi.evetech.net/latest"
USER_AGENT = "EVE Price Checker (kontakt@example.com)"

# Sales Tax: 7,5 % Basis, mit Accounting V → 3,375 %
# (bei anderem Skill-Level anpassen)
SALES_TAX = 0.03375

STATIONS = {
    "Jita 4-4":   {"station_id": 60003760, "region_id": 10000002},
    "Amarr VIII": {"station_id": 60008494, "region_id": 10000043},
    "Dodixie IX": {"station_id": 60011866, "region_id": 10000032},
    "Rens VI":    {"station_id": 60004588, "region_id": 10000030},
}

ROUTES_SHOWN = 3

MAX_SUGGESTIONS = 100


# ---------------------------------------------------------------------------
# HTTP / ESI
# ---------------------------------------------------------------------------
def make_session():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=10))
    return session


SESSION = make_session()


# ---------------------------------------------------------------------------
# Item-Datenbank (Name → TypeID), lokal gecacht
# ---------------------------------------------------------------------------
def build_item_db():
    """Alle handelbaren Items über ESI holen und Namen auflösen."""
    r = SESSION.get(f"{ESI}/markets/prices/", timeout=20)
    r.raise_for_status()
    type_ids = sorted({p["type_id"] for p in r.json()})

    items = {}
    for i in range(0, len(type_ids), 1000):
        r = SESSION.post(f"{ESI}/universe/names/", json=type_ids[i:i + 1000], timeout=20)
        r.raise_for_status()
        items.update(
            {e["name"]: e["id"] for e in r.json() if e.get("category") == "inventory_type"}
        )
    return items


def read_cache():
    """Liefert (items, alter_in_sekunden) oder (None, None)."""
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data:
            return None, None
        return data, time.time() - os.path.getmtime(CACHE_FILE)
    except (OSError, ValueError):
        return None, None


def save_cache(items):
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def load_item_db():
    """Gibt (items, hinweis) zurück. Hinweis ist None oder eine Warnung."""
    cached, age = read_cache()
    if cached and age < CACHE_MAX_AGE:
        return cached, None

    try:
        items = build_item_db()
        save_cache(items)
        return items, None
    except Exception as e:
        if cached:
            return cached, f"Item-Update fehlgeschlagen ({e.__class__.__name__}), nutze alten Cache"
        raise


# ---------------------------------------------------------------------------
# Marktdaten
# ---------------------------------------------------------------------------
def fetch_orders(region_id, type_id):
    url = f"{ESI}/markets/{region_id}/orders/"
    params = {"order_type": "all", "type_id": type_id, "page": 1}

    r = SESSION.get(url, params=params, timeout=10)
    r.raise_for_status()
    orders = r.json()

    pages = int(r.headers.get("X-Pages", 1))
    for page in range(2, pages + 1):
        params["page"] = page
        r = SESSION.get(url, params=params, timeout=10)
        r.raise_for_status()
        orders.extend(r.json())
    return orders


def fetch_market_prices(type_id, station_id, region_id):
    """Höchstes Buy / niedrigstes Sell direkt an der Station."""
    # Buy-Orders mit Range können auch woanders liegen – nur die Station selbst zählt.
    orders = fetch_orders(region_id, type_id)
    at_station = [o for o in orders if o["location_id"] == station_id]
    buys = [o["price"] for o in at_station if o["is_buy_order"]]
    sells = [o["price"] for o in at_station if not o["is_buy_order"]]
    return {
        "buy": max(buys) if buys else None,
        "sell": min(sells) if sells else None,
        "error": None,
    }


def safe_fetch(type_id, station):
    try:
        return fetch_market_prices(type_id, **STATIONS[station])
    except Exception as e:
        return {"buy": None, "sell": None, "error": str(e)[:120]}


def calc_route(src_sell, dst_buy):
    """Einkauf per Sell-Order an src, Verkauf an Buy-Order in dst. → (profit, prozent)"""
    if src_sell is None or dst_buy is None or src_sell <= 0:
        return None
    profit = dst_buy * (1 - SALES_TAX) - src_sell
    return profit, profit / src_sell * 100


def fmt_isk(value):
    return "–" if value is None else f"{value:,.2f} ISK"


# ---------------------------------------------------------------------------
# Eingebettete Grafiken (Base64-PNG)
# ---------------------------------------------------------------------------
LOGO_HEADER_PNG = (
    "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"
    "34DbWnGjAAAAAElFTkSuQmCC"
)

LOGO_ICON_PNG = (
    "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"
    "AAAASUVORK5CYII="
)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
C = {
    "bg":         "#121417",
    "surface":    "#1B1E22",
    "surface_hi": "#23272C",
    "border":     "#2E333A",
    "text":       "#E8EAED",
    "muted":      "#8B929C",
    "accent":     "#7FCFFF",
    "accent_hi":  "#A6DEFF",
    "accent_dim": "#4259FF",
    "on_accent":  "#0A0C0E",
    "pos":        "#3DFFA2",
    "neg":        "#FF4D6D",
    "warn":       "#FFB547",
}

STATUS_COLORS = {"ok": C["pos"], "busy": C["accent"], "warn": C["warn"], "err": C["neg"]}


def pick_font(candidates, fallback):
    available = set(tkfont.families())
    return next((f for f in candidates if f in available), fallback)


def apply_theme(root):
    ui = pick_font(["Segoe UI", "Inter", "Helvetica Neue", "DejaVu Sans"], "TkDefaultFont")
    mono = pick_font(["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono"], "TkFixedFont")

    root.configure(bg=C["bg"])
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=C["bg"], foreground=C["text"], font=(ui, 10),
                    bordercolor=C["border"], focuscolor=C["accent"])
    style.configure("App.TFrame", background=C["bg"])
    style.configure("Card.TFrame", background=C["surface"])

    style.configure("Title.TLabel", background=C["bg"], foreground=C["text"], font=(ui, 15, "bold"))
    style.configure("Subtitle.TLabel", background=C["bg"], foreground=C["muted"], font=(ui, 9))
    style.configure("Hint.TLabel", background=C["bg"], foreground=C["muted"], font=(ui, 9))
    style.configure("CardTitle.TLabel", background=C["surface"], foreground=C["accent"], font=(ui, 9, "bold"))
    style.configure("CardText.TLabel", background=C["surface"], foreground=C["muted"], font=(ui, 10))
    style.configure("Value.TLabel", background=C["surface"], foreground=C["text"], font=(mono, 11, "bold"))
    style.configure("ValuePos.TLabel", background=C["surface"], foreground=C["pos"], font=(mono, 11, "bold"))
    style.configure("ValueNeg.TLabel", background=C["surface"], foreground=C["neg"], font=(mono, 11, "bold"))
    style.configure("ValueBest.TLabel", background=C["surface"], foreground=C["accent"], font=(mono, 11, "bold"))
    style.configure("Status.TLabel", background=C["bg"], foreground=C["muted"], font=(ui, 9))
    style.configure("StatusDot.TLabel", background=C["bg"], foreground=C["muted"], font=(ui, 10))

    # Combobox
    style.configure("TCombobox", fieldbackground=C["surface"], background=C["surface"],
                    foreground=C["text"], arrowcolor=C["accent"], bordercolor=C["border"],
                    lightcolor=C["surface"], darkcolor=C["surface"], insertcolor=C["accent"],
                    selectbackground=C["accent_dim"], selectforeground=C["text"], padding=6)
    style.map("TCombobox",
              fieldbackground=[("disabled", C["bg"]), ("focus", C["surface_hi"])],
              foreground=[("disabled", C["muted"])],
              bordercolor=[("focus", C["accent"])],
              lightcolor=[("focus", C["surface_hi"])],
              darkcolor=[("focus", C["surface_hi"])],
              arrowcolor=[("disabled", C["muted"])],
              background=[("active", C["surface_hi"])])

    # Dropdown-Liste der Combobox
    root.option_add("*TCombobox*Listbox.background", C["surface"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["on_accent"])
    root.option_add("*TCombobox*Listbox.font", (ui, 10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.highlightThickness", 0)

    # Button
    style.configure("Accent.TButton", background=C["accent"], foreground=C["on_accent"],
                    bordercolor=C["accent"], lightcolor=C["accent"], darkcolor=C["accent"],
                    focusthickness=0, font=(ui, 10, "bold"), padding=(18, 6), relief="flat")
    style.map("Accent.TButton",
              background=[("disabled", C["surface_hi"]), ("pressed", C["accent_dim"]), ("active", C["accent_hi"])],
              foreground=[("disabled", C["muted"])],
              bordercolor=[("disabled", C["border"]), ("pressed", C["accent_dim"]), ("active", C["accent_hi"])],
              lightcolor=[("disabled", C["surface_hi"]), ("pressed", C["accent_dim"]), ("active", C["accent_hi"])],
              darkcolor=[("disabled", C["surface_hi"]), ("pressed", C["accent_dim"]), ("active", C["accent_hi"])])


def style_windows_titlebar(root):
    """Dunkle Titelleiste unter Windows 10/11 (DWM)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi
        # DWMWA_USE_IMMERSIVE_DARK_MODE (20, ältere Builds: 19)
        dark = ctypes.c_int(1)
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                break
        # DWMWA_CAPTION_COLOR (35, nur Windows 11) – Farbe als 0x00BBGGRR
        r, g, b = (int(C["bg"][i:i + 2], 16) for i in (1, 3, 5))
        caption = ctypes.c_int(r | (g << 8) | (b << 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class PriceCheckerApp:
    def __init__(self, root):
        self.root = root
        self.items = {}         # Name → TypeID
        self.lookup = {}        # name.lower() → Name
        self.names_lower = []   # [(name.lower(), Name), …] sortiert
        self.busy = False
        self.ui_queue = queue.Queue()

        apply_theme(root)

        self.logo_img = tk.PhotoImage(data=LOGO_HEADER_PNG)
        self.icon_img = tk.PhotoImage(data=LOGO_ICON_PNG)
        root.iconphoto(True, self.icon_img)
        self._build_ui()
        style_windows_titlebar(root)
        self._poll_queue()

        self.set_status("Lade Item-Datenbank …", "busy")
        threading.Thread(target=self._load_items_worker, daemon=True).start()

    # --- Thread → UI ------------------------------------------------------
    def call_in_ui(self, fn, *args):
        self.ui_queue.put((fn, args))

    def _poll_queue(self):
        while not self.ui_queue.empty():
            fn, args = self.ui_queue.get_nowait()
            fn(*args)
        self.root.after(100, self._poll_queue)

    def set_status(self, text, level="ok"):
        color = STATUS_COLORS.get(level, C["muted"])
        self.status_dot.config(foreground=color)
        self.status_label.config(
            text=text, foreground=color if level in ("err", "warn") else C["muted"])

    # --- UI-Bausteine -----------------------------------------------------
    @staticmethod
    def _card(parent, title):
        """Karte mit Akzentstreifen links. → (outer, inner)"""
        outer = tk.Frame(parent, bg=C["border"])
        tk.Frame(outer, bg=C["accent"], width=3).pack(side="left", fill="y")
        inner = ttk.Frame(outer, style="Card.TFrame", padding=(14, 10))
        inner.pack(side="left", fill="both", expand=True, padx=(0, 1), pady=1)
        inner.columnconfigure(1, weight=1)
        ttk.Label(inner, text=title.upper(), style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 6))

        return outer, inner

    @staticmethod
    def _value_row(card, row, text, width=18):
        name = ttk.Label(card, text=text, style="CardText.TLabel")
        name.grid(row=row, column=0, sticky=tk.W, pady=2)

        lbl = ttk.Label(card, text="–", style="Value.TLabel", width=width, anchor=tk.E)
        lbl.grid(row=row, column=1, sticky=tk.E, padx=(16, 0), pady=2)
        return name, lbl

    def _build_ui(self):
        self.root.title("EVE Price Checker")
        self.root.resizable(False, False)

        frame = ttk.Frame(self.root, style="App.TFrame", padding=(20, 18, 20, 12))
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)

        # Header
        header = ttk.Frame(frame, style="App.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky=tk.EW)
        header.columnconfigure(1, weight=1)
        tk.Label(header, image=self.logo_img, bg=C["bg"], bd=0).grid(row=0, column=0, rowspan=2, sticky=tk.W)
        ttk.Label(header, text="EVE PRICE CHECKER", style="Title.TLabel").grid(row=0, column=1, sticky=tk.SE)
        ttk.Label(header, text="  ·  ".join(STATIONS), style="Subtitle.TLabel").grid(row=1, column=1, sticky=tk.NE)
        tk.Frame(frame, bg=C["accent"], height=2).grid(
            row=2, column=0, columnspan=2, sticky=tk.EW, pady=(10, 14))

        # Suche
        ttk.Label(frame, text="Artikel suchen – tippen, ↓ für Vorschläge", style="Hint.TLabel").grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=(0, 4))

        self.combo = ttk.Combobox(frame, width=40, state="disabled")
        self.combo.grid(row=4, column=0, sticky=tk.EW, pady=(0, 16))
        self.combo.bind("<KeyRelease>", self._on_keyrelease)
        self.combo.bind("<<ComboboxSelected>>", self.update_prices)
        self.combo.bind("<Return>", self.update_prices)
        self.combo.bind("<KP_Enter>", self.update_prices)

        self.fetch_btn = ttk.Button(frame, text="ABRUFEN", style="Accent.TButton",
                                    command=self.update_prices, state="disabled", cursor="hand2")
        self.fetch_btn.grid(row=4, column=1, padx=(10, 0), pady=(0, 16), sticky=tk.NSEW)

        # Stations-Karten (2 Spalten)
        grid = ttk.Frame(frame, style="App.TFrame")
        grid.grid(row=5, column=0, columnspan=2, sticky=tk.EW)
        grid.columnconfigure((0, 1), weight=1, uniform="cards")

        self.value_labels = {}
        for i, station in enumerate(STATIONS):
            outer, card = self._card(grid, station)
            outer.grid(row=i // 2, column=i % 2, sticky=tk.NSEW,
                       padx=(0, 5) if i % 2 == 0 else (5, 0), pady=(0, 10))
            _, self.value_labels[(station, "buy")] = self._value_row(card, 1, "Höchstes Buy")
            _, self.value_labels[(station, "sell")] = self._value_row(card, 2, "Niedrigstes Sell")

        # Arbitrage-Routen
        outer, card = self._card(frame, f"Beste Arbitrage-Routen  ·  {SALES_TAX * 100:.2f} % Sales Tax")
        outer.grid(row=6, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))
        self.route_rows = [self._value_row(card, r, "–", width=28) for r in range(1, ROUTES_SHOWN + 1)]
        row = 7

        # Statuszeile
        tk.Frame(frame, bg=C["border"], height=1).grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=(4, 8))

        status = ttk.Frame(frame, style="App.TFrame")
        status.grid(row=row + 1, column=0, columnspan=2, sticky=tk.EW)
        self.status_dot = ttk.Label(status, text="●", style="StatusDot.TLabel")
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_label = ttk.Label(status, text="", style="Status.TLabel", wraplength=400)
        self.status_label.pack(side="left", fill="x")

    # --- Item-Datenbank -----------------------------------------------------
    def _load_items_worker(self):
        try:
            items, note = load_item_db()
            self.call_in_ui(self._on_items_loaded, items, note)
        except Exception as e:
            self.call_in_ui(self._on_items_failed, e)

    def _on_items_loaded(self, items, note):
        self.items = items
        self.lookup = {name.lower(): name for name in items}
        self.names_lower = sorted((name.lower(), name) for name in items)

        self.combo.config(state="normal", values=[n for _, n in self.names_lower[:MAX_SUGGESTIONS]])
        self.fetch_btn.config(state="normal")
        self.combo.focus_set()

        if note:
            self.set_status(note, "warn")
        else:
            self.set_status(f"Bereit · {len(items):,} Items geladen", "ok")

    def _on_items_failed(self, err):
        self.set_status("Item-Datenbank konnte nicht geladen werden.", "err")
        messagebox.showerror("Fehler", f"Konnte Item-Datenbank nicht laden:\n{err}", parent=self.root)

    # --- Suche --------------------------------------------------------------
    def _on_keyrelease(self, event):
        if event.keysym in ("Up", "Down", "Left", "Right", "Home", "End",
                            "Return", "KP_Enter", "Escape", "Tab"):
            # Navigationstasten nicht als Eingabe werten,
            # sonst springt die Vorschlagsliste beim Blättern zurück
            return

        value = self.combo.get().strip().lower()
        if not value:
            self.combo["values"] = [n for _, n in self.names_lower[:MAX_SUGGESTIONS]]
            return

        # Treffer am Wortanfang zuerst, dann Teilstring-Treffer
        prefix, contains = [], []
        for low, name in self.names_lower:
            if low.startswith(value):
                prefix.append(name)
            elif value in low:
                contains.append(name)
        self.combo["values"] = (prefix + contains)[:MAX_SUGGESTIONS]

    # --- Preise -------------------------------------------------------------
    def update_prices(self, event=None):
        if self.busy or not self.items:
            return

        name = self.lookup.get(self.combo.get().strip().lower())
        if not name:
            self.set_status("Bitte ein gültiges Item aus der Liste wählen!", "err")
            return

        self.combo.set(name)
        type_id = self.items[name]

        self.busy = True
        self.fetch_btn.config(state="disabled")
        self.set_status(f"Lade Daten für {name} …", "busy")
        threading.Thread(target=self._fetch_worker, args=(name, type_id), daemon=True).start()

    def _fetch_worker(self, name, type_id):
        with ThreadPoolExecutor(max_workers=len(STATIONS)) as ex:
            futures = {st: ex.submit(safe_fetch, type_id, st) for st in STATIONS}
            results = {st: f.result() for st, f in futures.items()}
        self.call_in_ui(self._render, name, type_id, results)

    def _render(self, name, type_id, results):
        errors = [f"{st}: {res['error']}" for st, res in results.items() if res["error"]]

        buys = [r["buy"] for r in results.values() if r["buy"] is not None]
        sells = [r["sell"] for r in results.values() if r["sell"] is not None]
        best_buy = max(buys) if buys else None
        best_sell = min(sells) if sells else None

        # Bestwerte über alle Hubs hervorheben
        for station, res in results.items():
            for key, best in (("buy", best_buy), ("sell", best_sell)):
                is_best = res[key] is not None and res[key] == best and len(results) > 1
                self.value_labels[(station, key)].config(
                    text=fmt_isk(res[key]), style="ValueBest.TLabel" if is_best else "Value.TLabel")

        # Alle Hub-Paare durchrechnen
        routes = []
        for src in results:
            for dst in results:
                if src == dst:
                    continue
                route = calc_route(results[src]["sell"], results[dst]["buy"])
                if route:
                    routes.append((route[0], route[1], src, dst))
        routes.sort(reverse=True)

        for i, (name_lbl, value_lbl) in enumerate(self.route_rows):
            if i < len(routes):
                profit, pct, src, dst = routes[i]
                name_lbl.config(text=f"{src.split()[0]} → {dst.split()[0]}")
                value_lbl.config(
                    text=f"{profit:+,.2f} ISK ({pct:+.1f} %)",
                    style="ValuePos.TLabel" if profit > 0 else "ValueNeg.TLabel",
                )
            else:
                name_lbl.config(text="–")
                value_lbl.config(text="–", style="Value.TLabel")

        self.busy = False
        self.fetch_btn.config(state="normal")

        if errors:
            self.set_status("Fehler – " + " | ".join(errors), "err")
        else:
            self.set_status(
                f"Bereit · {name} · TypeID {type_id} · {time.strftime('%H:%M:%S')}", "ok")


def main():
    root = tk.Tk()
    PriceCheckerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
