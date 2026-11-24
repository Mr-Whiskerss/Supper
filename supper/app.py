"""Supper: a small self-hosted meal planner for a household.

Run locally:   python app.py
Run in Docker: docker compose up -d
"""
import datetime as dt
import html
import json
import os
import random
import re
import sqlite3
import urllib.error
import urllib.request
from html.parser import HTMLParser

from flask import Flask, g, jsonify, request, send_from_directory

from seed_recipes import SEED_RECIPES

DB_PATH = os.environ.get("SUPPER_DB", os.path.join(os.path.dirname(__file__), "data", "supper.db"))
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB uploads (JSON backups)

# --------------------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT DEFAULT '',
    ingredients   TEXT DEFAULT '[]',
    steps         TEXT DEFAULT '[]',
    tags          TEXT DEFAULT '[]',
    prep_minutes  INTEGER,
    servings      INTEGER,
    source_url    TEXT DEFAULT '',
    image_url     TEXT DEFAULT '',
    notes         TEXT DEFAULT '',
    favorite      INTEGER DEFAULT 0,
    is_builtin    INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS plan_entries (
    day        TEXT NOT NULL,          -- YYYY-MM-DD
    meal       TEXT NOT NULL,          -- lunch | dinner
    recipe_id  INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    note       TEXT DEFAULT '',        -- e.g. "Eating out", "Leftovers"
    locked     INTEGER DEFAULT 0,
    PRIMARY KEY (day, meal)
);
CREATE TABLE IF NOT EXISTS shopping_state (
    week_start TEXT NOT NULL,
    item_key   TEXT NOT NULL,
    checked    INTEGER DEFAULT 0,
    PRIMARY KEY (week_start, item_key)
);
CREATE TABLE IF NOT EXISTS shopping_extras (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,
    text       TEXT NOT NULL,
    checked    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

MEALS = ("lunch", "dinner")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    seeded = db.execute("SELECT value FROM settings WHERE key='seeded'").fetchone()
    if not seeded:
        for r in SEED_RECIPES:
            insert_recipe(db, r, builtin=True)
        db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('seeded', '1')")
    db.commit()
    db.close()


def as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def as_int(value):
    try:
        n = int(float(value))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def clean_recipe(data):
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("Give the recipe a title.")
    tags = [t.strip().lower() for t in as_list(data.get("tags")) if t.strip()]
    if isinstance(data.get("tags"), str) and "," in data["tags"]:
        tags = [t.strip().lower() for t in data["tags"].split(",") if t.strip()]
    return {
        "title": title[:200],
        "description": (data.get("description") or "").strip(),
        "ingredients": as_list(data.get("ingredients")),
        "steps": as_list(data.get("steps")),
        "tags": sorted(set(tags)),
        "prep_minutes": as_int(data.get("prep_minutes")),
        "servings": as_int(data.get("servings")),
        "source_url": (data.get("source_url") or "").strip(),
        "image_url": (data.get("image_url") or "").strip(),
        "notes": (data.get("notes") or "").strip(),
        "favorite": 1 if data.get("favorite") else 0,
    }


def insert_recipe(db, data, builtin=False):
    r = clean_recipe(data)
    cur = db.execute(
        """INSERT INTO recipes (title, description, ingredients, steps, tags, prep_minutes, servings,
                                source_url, image_url, notes, favorite, is_builtin)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (r["title"], r["description"], json.dumps(r["ingredients"]), json.dumps(r["steps"]),
         json.dumps(r["tags"]), r["prep_minutes"], r["servings"], r["source_url"], r["image_url"],
         r["notes"], r["favorite"], 1 if builtin else 0),
    )
    return cur.lastrowid


def row_to_recipe(row):
    d = dict(row)
    for k in ("ingredients", "steps", "tags"):
        d[k] = json.loads(d[k] or "[]")
    d["favorite"] = bool(d["favorite"])
    d["is_builtin"] = bool(d["is_builtin"])
    return d


def get_recipe(db, rid):
    row = db.execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
    return row_to_recipe(row) if row else None


def bad(msg, code=400):
    return jsonify({"error": msg}), code


# --------------------------------------------------------------------------- recipes API

@app.get("/api/recipes")
def list_recipes():
    db = get_db()
    rows = db.execute("SELECT * FROM recipes ORDER BY favorite DESC, title COLLATE NOCASE").fetchall()
    recipes = [row_to_recipe(r) for r in rows]
    last = {r["recipe_id"]: r["last_day"] for r in db.execute(
        "SELECT recipe_id, MAX(day) AS last_day FROM plan_entries "
        "WHERE recipe_id IS NOT NULL AND day <= date('now') GROUP BY recipe_id")}
    for r in recipes:
        r["last_planned"] = last.get(r["id"])
    return jsonify(recipes)


@app.post("/api/recipes")
def create_recipe():
    db = get_db()
    try:
        rid = insert_recipe(db, request.get_json(force=True))
    except ValueError as e:
        return bad(str(e))
    db.commit()
    return jsonify(get_recipe(db, rid)), 201


@app.put("/api/recipes/<int:rid>")
def update_recipe(rid):
    db = get_db()
    if not get_recipe(db, rid):
        return bad("That recipe no longer exists.", 404)
    try:
        r = clean_recipe(request.get_json(force=True))
    except ValueError as e:
        return bad(str(e))
    db.execute(
        """UPDATE recipes SET title=?, description=?, ingredients=?, steps=?, tags=?, prep_minutes=?,
           servings=?, source_url=?, image_url=?, notes=?, favorite=? WHERE id=?""",
        (r["title"], r["description"], json.dumps(r["ingredients"]), json.dumps(r["steps"]),
         json.dumps(r["tags"]), r["prep_minutes"], r["servings"], r["source_url"], r["image_url"],
         r["notes"], r["favorite"], rid),
    )
    db.commit()
    return jsonify(get_recipe(db, rid))


@app.post("/api/recipes/<int:rid>/favorite")
def toggle_favorite(rid):
    db = get_db()
    db.execute("UPDATE recipes SET favorite = 1 - favorite WHERE id=?", (rid,))
    db.commit()
    r = get_recipe(db, rid)
    return jsonify(r) if r else bad("That recipe no longer exists.", 404)


@app.delete("/api/recipes/<int:rid>")
def delete_recipe(rid):
    db = get_db()
    db.execute("UPDATE plan_entries SET recipe_id=NULL WHERE recipe_id=?", (rid,))
    db.execute("DELETE FROM plan_entries WHERE recipe_id IS NULL AND note=''")
    db.execute("DELETE FROM recipes WHERE id=?", (rid,))
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- importers

class _LdJsonCollector(HTMLParser):
    """Collects <script type="application/ld+json"> blocks and a few meta tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.meta, self._in_ld, self._buf = [], {}, False, []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower().strip() == "application/ld+json":
            self._in_ld, self._buf = True, []
        elif tag == "meta":
            key = a.get("property") or a.get("name")
            if key and a.get("content"):
                self.meta[key.lower()] = a["content"]
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self.blocks.append("".join(self._buf))
            self._in_ld = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)
        elif self._in_title:
            self.title += data


def _find_recipe_node(node):
    if isinstance(node, list):
        for item in node:
            found = _find_recipe_node(item)
            if found:
                return found
    elif isinstance(node, dict):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Recipe" in types:
            return node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                found = _find_recipe_node(node[key])
                if found:
                    return found
    return None


def _iso_minutes(value):
    if not value or not isinstance(value, str):
        return None
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", value.strip())
    if not m:
        return None
    d, h, mins = (int(x) if x else 0 for x in m.groups())
    total = d * 1440 + h * 60 + mins
    return total or None


def _text(value):
    return html.unescape(re.sub(r"<[^>]+>", " ", str(value))).strip() if value else ""


def _instructions(value):
    out = []
    if isinstance(value, str):
        parts = re.split(r"\n+|(?<=\.)\s{2,}", _text(value))
        out.extend(p.strip() for p in parts if p.strip())
    elif isinstance(value, list):
        for item in value:
            out.extend(_instructions(item))
    elif isinstance(value, dict):
        if value.get("itemListElement"):
            out.extend(_instructions(value["itemListElement"]))
        elif value.get("text") or value.get("name"):
            out.append(_text(value.get("text") or value.get("name")))
    return [re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", s)) for s in out if s]


def _image(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return _image(value[0])
    if isinstance(value, dict):
        return value.get("url") or value.get("contentUrl") or ""
    return ""


def _split_tags(*values):
    tags = []
    for v in values:
        if isinstance(v, str):
            tags.extend(v.split(","))
        elif isinstance(v, list):
            for x in v:
                tags.extend(str(x).split(","))
    clean = []
    for t in tags:
        t = _text(t).lower()
        if t and len(t) <= 24 and t not in clean:
            clean.append(t)
    return clean[:6]


def parse_recipe_html(page_html, url):
    p = _LdJsonCollector()
    p.feed(page_html)
    node = None
    for block in p.blocks:
        try:
            node = _find_recipe_node(json.loads(block.strip()))
        except json.JSONDecodeError:
            continue
        if node:
            break
    if not node:
        return None
    yield_ = node.get("recipeYield")
    if isinstance(yield_, list):
        yield_ = yield_[0] if yield_ else None
    servings = None
    if yield_:
        m = re.search(r"\d+", str(yield_))
        servings = int(m.group()) if m else None
    minutes = _iso_minutes(node.get("totalTime")) or (
        (_iso_minutes(node.get("prepTime")) or 0) + (_iso_minutes(node.get("cookTime")) or 0)) or None
    return {
        "title": _text(node.get("name")) or _text(p.meta.get("og:title")) or p.title.strip(),
        "description": _text(node.get("description")),
        "ingredients": [re.sub(r"\s+", " ", _text(i)) for i in node.get("recipeIngredient") or node.get("ingredients") or []],
        "steps": _instructions(node.get("recipeInstructions")),
        "tags": _split_tags(node.get("recipeCategory"), node.get("recipeCuisine"), node.get("keywords")),
        "prep_minutes": minutes,
        "servings": servings,
        "image_url": _image(node.get("image")) or p.meta.get("og:image", ""),
        "source_url": url,
    }


@app.post("/api/import/url")
def import_url():
    url = ((request.get_json(force=True) or {}).get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return bad("Paste a full web address starting with http:// or https://.")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            page = resp.read(5_000_000).decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        return bad(f"The site refused the request (error {e.code}). Copy the recipe text and use Paste text instead.", 502)
    except Exception:
        return bad("Couldn’t reach that page. Check the address, or copy the recipe text and use Paste text instead.", 502)
    recipe = parse_recipe_html(page, url)
    if not recipe or not recipe["title"]:
        return bad("No recipe found on that page. Copy the recipe text and use Paste text instead.", 422)
    return jsonify(recipe)


HEAD_ING = re.compile(r"^\W*(ingredients?|you(?:’|')?ll need|shopping list)\b\W*$", re.I)
HEAD_STEP = re.compile(r"^\W*(method|instructions?|directions?|steps?|preparation|how to make( it)?)\b\W*$", re.I)


def parse_recipe_text(text):
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return None
    title, rest = lines[0].lstrip("#").strip(), lines[1:]
    section, desc, ings, steps = "desc", [], [], []
    for ln in rest:
        if HEAD_ING.match(ln):
            section = "ing"
            continue
        if HEAD_STEP.match(ln):
            section = "step"
            continue
        item = re.sub(r"^\s*(?:[-*•▢□◦·]|\d+[.)]|step\s*\d+[:.)]?)\s*", "", ln, flags=re.I)
        if not item:
            continue
        {"desc": desc, "ing": ings, "step": steps}[section].append(item)
    desc = [d for d in desc if not re.match(r"^(serves|makes|prep|cook|takes|total time|time)\b", d, re.I)]
    # No headings found: treat short lines as ingredients and long ones as steps.
    if not ings and not steps:
        for ln in desc:
            (ings if len(ln) < 60 else steps).append(ln)
        desc = []
    minutes = None
    m = re.search(r"(\d+)\s*(?:min|minutes)\b", text, re.I)
    if m:
        minutes = int(m.group(1))
    serves = None
    m = re.search(r"serves\s*(\d+)", text, re.I)
    if m:
        serves = int(m.group(1))
    return {"title": title, "description": " ".join(desc)[:500], "ingredients": ings,
            "steps": steps, "tags": [], "prep_minutes": minutes, "servings": serves,
            "image_url": "", "source_url": ""}


@app.post("/api/import/text")
def import_text():
    text = (request.get_json(force=True) or {}).get("text") or ""
    recipe = parse_recipe_text(text)
    if not recipe:
        return bad("Paste the recipe text first.")
    return jsonify(recipe)


@app.post("/api/import/json")
def import_json():
    data = request.get_json(force=True)
    items = data.get("recipes") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return bad("That file isn’t a Supper backup or a list of recipes.")
    db = get_db()
    existing = {r["title"].lower() for r in db.execute("SELECT title FROM recipes")}
    added, skipped = 0, 0
    for item in items:
        if not isinstance(item, dict) or not item.get("title"):
            skipped += 1
            continue
        if item["title"].strip().lower() in existing:
            skipped += 1
            continue
        insert_recipe(db, item)
        existing.add(item["title"].strip().lower())
        added += 1
    db.commit()
    return jsonify({"added": added, "skipped": skipped})


@app.get("/api/export")
def export_all():
    db = get_db()
    recipes = [row_to_recipe(r) for r in db.execute("SELECT * FROM recipes ORDER BY id")]
    for r in recipes:
        r.pop("id"), r.pop("is_builtin")
    plan = [dict(r) for r in db.execute(
        "SELECT p.day, p.meal, p.note, r.title AS recipe FROM plan_entries p "
        "LEFT JOIN recipes r ON r.id = p.recipe_id ORDER BY p.day")]
    resp = jsonify({"app": "supper", "version": 1, "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "recipes": recipes, "plan": plan})
    resp.headers["Content-Disposition"] = f'attachment; filename="supper-backup-{dt.date.today()}.json"'
    return resp


# --------------------------------------------------------------------------- planner

def week_start(value=None):
    try:
        d = dt.date.fromisoformat(value) if value else dt.date.today()
    except ValueError:
        d = dt.date.today()
    return d - dt.timedelta(days=d.weekday())  # Monday


def week_days(start):
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(7)]


def get_settings(db):
    rows = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}
    return {"meals": json.loads(rows.get("meals", '["dinner"]')),
            "prefs": json.loads(rows.get("prefs", "{}"))}


@app.get("/api/settings")
def read_settings():
    return jsonify(get_settings(get_db()))


@app.put("/api/settings")
def write_settings():
    data = request.get_json(force=True) or {}
    db = get_db()
    if "meals" in data:
        meals = [m for m in MEALS if m in (data.get("meals") or [])] or ["dinner"]
        db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('meals', ?)", (json.dumps(meals),))
    if isinstance(data.get("prefs"), dict):
        db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('prefs', ?)", (json.dumps(data["prefs"]),))
    db.commit()
    return jsonify(get_settings(db))


def plan_payload(db, start):
    days = week_days(start)
    rows = db.execute(
        "SELECT * FROM plan_entries WHERE day BETWEEN ? AND ?", (days[0], days[-1])).fetchall()
    entries = {}
    for r in rows:
        e = dict(r)
        e["locked"] = bool(e["locked"])
        e["recipe"] = get_recipe(db, e["recipe_id"]) if e["recipe_id"] else None
        entries[f'{e["day"]}|{e["meal"]}'] = e
    return {"start": days[0], "days": days, "entries": entries, "settings": get_settings(db)}


@app.get("/api/plan")
def read_plan():
    return jsonify(plan_payload(get_db(), week_start(request.args.get("start"))))


@app.put("/api/plan/<day>/<meal>")
def set_entry(day, meal):
    if meal not in MEALS:
        return bad("Unknown meal.")
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        return bad("Unknown date.")
    data = request.get_json(force=True) or {}
    db = get_db()
    current = db.execute("SELECT * FROM plan_entries WHERE day=? AND meal=?", (day, meal)).fetchone()
    recipe_id = data["recipe_id"] if "recipe_id" in data else (current["recipe_id"] if current else None)
    note = data["note"] if "note" in data else (current["note"] if current else "")
    locked = data["locked"] if "locked" in data else (current["locked"] if current else 0)
    if recipe_id is None and not note:
        db.execute("DELETE FROM plan_entries WHERE day=? AND meal=?", (day, meal))
    else:
        db.execute("INSERT OR REPLACE INTO plan_entries(day, meal, recipe_id, note, locked) VALUES (?,?,?,?,?)",
                   (day, meal, recipe_id, (note or "").strip(), 1 if locked else 0))
    db.commit()
    return jsonify(plan_payload(db, week_start(day)))


@app.delete("/api/plan/<day>/<meal>")
def clear_entry(day, meal):
    db = get_db()
    db.execute("DELETE FROM plan_entries WHERE day=? AND meal=?", (day, meal))
    db.commit()
    return jsonify(plan_payload(db, week_start(day)))


def candidate_pool(db, opts, day_index, exclude_ids):
    recipes = [row_to_recipe(r) for r in db.execute("SELECT * FROM recipes")]
    include = set(opts.get("include_tags") or [])
    avoid = set(opts.get("exclude_tags") or [])
    weeknight_max = as_int(opts.get("weeknight_max_minutes"))
    pool = []
    for r in recipes:
        tags = set(r["tags"])
        if include and not (tags & include):
            continue
        if tags & avoid:
            continue
        if opts.get("favorites_only") and not r["favorite"]:
            continue
        if weeknight_max and day_index < 5 and r["prep_minutes"] and r["prep_minutes"] > weeknight_max:
            continue
        pool.append(r)
    fresh = [r for r in pool if r["id"] not in exclude_ids]
    return fresh or pool


def weighted_pick(pool, prefer_favorites):
    weights = [3 if (prefer_favorites and r["favorite"]) else 1 for r in pool]
    return random.choices(pool, weights=weights, k=1)[0]


@app.post("/api/plan/generate")
def generate_plan():
    opts = request.get_json(force=True) or {}
    db = get_db()
    start = week_start(opts.get("start"))
    days = week_days(start)
    meals = [m for m in MEALS if m in get_settings(db)["meals"]]
    only_day = opts.get("day")
    only_meal = opts.get("meal")
    mode = opts.get("mode", "fill")  # fill = empty slots only, shuffle = all unlocked recipe slots

    recent_cutoff = (start - dt.timedelta(days=14)).isoformat()
    used = {r["recipe_id"] for r in db.execute(
        "SELECT recipe_id FROM plan_entries WHERE day >= ? AND day < ? AND recipe_id IS NOT NULL",
        (recent_cutoff, days[0]))}
    current = {f'{r["day"]}|{r["meal"]}': r for r in db.execute(
        "SELECT * FROM plan_entries WHERE day BETWEEN ? AND ?", (days[0], days[-1]))}
    targets = []
    for i, day in enumerate(days):
        for meal in meals:
            if only_day and (day != only_day or meal != only_meal):
                continue
            e = current.get(f"{day}|{meal}")
            if e and e["locked"]:
                used.add(e["recipe_id"])
                continue
            if e and e["note"] and not e["recipe_id"]:
                continue  # "Eating out" etc. stays
            if mode == "fill" and e and e["recipe_id"] and not only_day:
                used.add(e["recipe_id"])
                continue
            targets.append((i, day, meal, e["recipe_id"] if e else None))

    # Keep everything else in the week out of the pool so the week stays varied.
    for key, e in current.items():
        if e["recipe_id"] and not any(f"{d}|{m}" == key for _, d, m, _ in targets):
            used.add(e["recipe_id"])

    picked = 0
    for i, day, meal, old_id in targets:
        exclude = set(used)
        if old_id:
            exclude.add(old_id)  # a reroll should give something different
        pool = candidate_pool(db, opts, i, exclude)
        if not pool:
            continue
        choice = weighted_pick(pool, opts.get("prefer_favorites", True))
        used.add(choice["id"])
        db.execute("INSERT OR REPLACE INTO plan_entries(day, meal, recipe_id, note, locked) VALUES (?,?,?,?,0)",
                   (day, meal, choice["id"], ""))
        picked += 1
    db.commit()
    payload = plan_payload(db, start)
    payload["picked"] = picked
    return jsonify(payload)


@app.post("/api/suggest")
def suggest():
    opts = request.get_json(force=True) or {}
    db = get_db()
    exclude = set(opts.get("exclude_ids") or [])
    pool = candidate_pool(db, opts, 6, exclude)
    if not pool:
        return bad("No recipes match those filters.", 404)
    return jsonify(weighted_pick(pool, opts.get("prefer_favorites", True)))


# --------------------------------------------------------------------------- shopping list

QTY = re.compile(
    r"^(?P<qty>\d+\s+\d/\d|\d+/\d+|\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>kg|g|ml|l|litres?|tbsp|tsp|x)?\b\s*(?P<name>.+)$", re.I)


def _qty_value(q):
    q = q.replace(",", ".").strip()
    if " " in q:
        whole, frac = q.split()
        a, b = frac.split("/")
        return int(whole) + int(a) / int(b)
    if "/" in q:
        a, b = q.split("/")
        return int(a) / int(b) if int(b) else 0
    return float(q)


def _fmt_qty(v):
    if abs(v - round(v)) < 0.01:
        return str(int(round(v)))
    for frac, label in ((0.25, "¼"), (0.5, "½"), (0.75, "¾"), (1 / 3, "⅓"), (2 / 3, "⅔")):
        if abs(v - int(v) - frac) < 0.02:
            return (str(int(v)) if int(v) else "") + label
    return f"{v:.1f}"


def _item_key(name):
    base = re.split(r"[,(]", name.lower())[0]
    base = re.sub(r"[^a-z0-9 ]", " ", base)
    words = [w for w in base.split() if w]
    if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def build_shopping(db, start):
    days = week_days(start)
    rows = db.execute(
        "SELECT p.recipe_id, r.title, r.ingredients FROM plan_entries p JOIN recipes r ON r.id = p.recipe_id "
        "WHERE p.day BETWEEN ? AND ?", (days[0], days[-1])).fetchall()
    groups = {}
    for row in rows:
        for line in json.loads(row["ingredients"] or "[]"):
            m = QTY.match(line)
            if m and m.group("unit") and m.group("unit").lower() == "x":
                m = None  # "2 x 400g tins" - keep as written
            if m:
                name, unit = m.group("name").strip(), (m.group("unit") or "").lower()
                try:
                    qty = _qty_value(m.group("qty"))
                except (ValueError, ZeroDivisionError):
                    qty, unit, name = None, "", line
            else:
                qty, unit, name = None, "", line
            key = _item_key(name) or line.lower()
            gp = groups.setdefault(key, {"key": key, "name": name, "amounts": {}, "loose": [], "recipes": []})
            if row["title"] not in gp["recipes"]:
                gp["recipes"].append(row["title"])
            if qty is not None:
                if unit in ("kg",):
                    unit, qty = "g", qty * 1000
                elif unit in ("l", "litre", "litres"):
                    unit, qty = "ml", qty * 1000
                gp["amounts"][unit] = gp["amounts"].get(unit, 0) + qty
                gp["lines"] = gp.get("lines", 0) + 1
            else:
                if line not in gp["loose"]:
                    gp["loose"].append(line)
    checked = {r["item_key"] for r in db.execute(
        "SELECT item_key FROM shopping_state WHERE week_start=? AND checked=1", (days[0],))}
    items = []
    for gp in groups.values():
        parts = []
        for unit, v in gp["amounts"].items():
            if unit in ("g", "ml") and v >= 1000:
                parts.append(f"{_fmt_qty(v / 1000)}{'kg' if unit == 'g' else ' litres' if v > 1000 else ' litre'}")
            else:
                parts.append(f"{_fmt_qty(v)}{unit if unit in ('g', 'ml') else (' ' + unit if unit else '')}")
        name = gp["name"]
        if gp.get("lines", 0) > 1:
            name = re.split(r"[,(]", name)[0].strip()
            only_count = list(gp["amounts"]) == [""]
            if only_count and gp["amounts"][""] > 1 and not name.endswith("s"):
                name += "s"
        if parts:
            text = f'{" + ".join(parts)} {name}'
            extra = [l for l in gp["loose"] if _item_key(l) != gp["key"]]
            if extra:
                text += f' (also: {"; ".join(extra)})'
        else:
            text = "; ".join(gp["loose"])
        items.append({"key": gp["key"], "text": text, "recipes": gp["recipes"], "checked": gp["key"] in checked})
    items.sort(key=lambda i: (i["checked"], i["key"]))
    extras = [dict(r) for r in db.execute(
        "SELECT id, text, checked FROM shopping_extras WHERE week_start=? ORDER BY id", (days[0],))]
    for e in extras:
        e["checked"] = bool(e["checked"])
    return {"start": days[0], "items": items, "extras": extras}


@app.get("/api/shopping")
def shopping():
    return jsonify(build_shopping(get_db(), week_start(request.args.get("start"))))


@app.post("/api/shopping/check")
def shopping_check():
    data = request.get_json(force=True) or {}
    start = week_start(data.get("start")).isoformat()
    db = get_db()
    if data.get("extra_id"):
        db.execute("UPDATE shopping_extras SET checked=? WHERE id=?", (1 if data.get("checked") else 0, data["extra_id"]))
    else:
        db.execute("INSERT OR REPLACE INTO shopping_state(week_start, item_key, checked) VALUES (?,?,?)",
                   (start, data.get("key", ""), 1 if data.get("checked") else 0))
    db.commit()
    return jsonify(build_shopping(db, week_start(start)))


@app.post("/api/shopping/extra")
def shopping_extra():
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    start = week_start(data.get("start")).isoformat()
    db = get_db()
    if text:
        db.execute("INSERT INTO shopping_extras(week_start, text) VALUES (?,?)", (start, text[:200]))
        db.commit()
    return jsonify(build_shopping(db, week_start(start)))


@app.delete("/api/shopping/extra/<int:eid>")
def shopping_extra_delete(eid):
    db = get_db()
    row = db.execute("SELECT week_start FROM shopping_extras WHERE id=?", (eid,)).fetchone()
    db.execute("DELETE FROM shopping_extras WHERE id=?", (eid,))
    db.commit()
    return jsonify(build_shopping(db, week_start(row["week_start"] if row else None)))


# --------------------------------------------------------------------------- pages

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def health():
    return "ok"


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)
