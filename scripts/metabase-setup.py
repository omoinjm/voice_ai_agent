"""One-off Metabase setup on the server: admin login, the read-only `dashboard` connection to the call
records, and a "Bookings" dashboard. Safe to re-run: it skips whatever already exists.

    scripts/deploy-server.sh run 'python3 scripts/metabase-setup.py'

Reads METABASE_ADMIN_EMAIL (default below), METABASE_ADMIN_PASSWORD and DASHBOARD_DB_PASSWORD from .env.
Uses only the standard library (it runs with the server's python3).
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

MB = "http://localhost:3000"
env = dict(
    line.split("=", 1) for line in Path(".env").read_text().splitlines() if "=" in line and not line.startswith("#")
)
ADMIN_EMAIL = env.get("METABASE_ADMIN_EMAIL", "admin@hammerandanvil.co.za")
ADMIN_PASSWORD = env["METABASE_ADMIN_PASSWORD"]


def api(method, path, body=None, session=None):
    req = urllib.request.Request(MB + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if session:
        req.add_header("X-Metabase-Session", session)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path}: {e.code} {e.read()[:300]!r}")


for _ in range(60):
    try:
        if api("GET", "/api/health").get("status") == "ok":
            break
    except (SystemExit, urllib.error.URLError, ConnectionError):
        pass
    time.sleep(5)
else:
    raise SystemExit("Metabase didn't become healthy")

db_details = {"host": "db", "port": 5432, "dbname": "voice", "user": "dashboard",
              "password": env["DASHBOARD_DB_PASSWORD"], "ssl": False}
props = api("GET", "/api/session/properties")
if props.get("setup-token") and not props.get("has-user-setup"):
    api("POST", "/api/setup", {
        "token": props["setup-token"],
        "user": {"email": ADMIN_EMAIL, "first_name": "Voice", "last_name": "Agent", "password": ADMIN_PASSWORD,
                 "site_name": "Voice Agent"},
        "prefs": {"site_name": "Voice Agent", "site_locale": "en", "allow_tracking": False},
        "database": {"engine": "postgres", "name": "Voice agent", "details": db_details},
    })
    print("Admin created:", ADMIN_EMAIL)

session = api("POST", "/api/session", {"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD})["id"]
dbs = api("GET", "/api/database", session=session)["data"]
db = next((d for d in dbs if d["name"] == "Voice agent"), None)
if not db:
    db = api("POST", "/api/database", {"engine": "postgres", "name": "Voice agent", "details": db_details},
             session=session)
db_id = db["id"]
# Hide Metabase's bundled sample data.
for d in dbs:
    if d.get("is_sample"):
        api("DELETE", f"/api/database/{d['id']}", session=session)

TZ = "Africa/Johannesburg"
CARDS = [
    ("Bookings", "table", {}, 18, 9, f"""
select b.created_at at time zone '{TZ}' as booked_at, b.reference, b.name, b.service,
       b.requested as requested_time, b.phone, b.status, b.call_id
from bookings b order by b.created_at desc"""),
    ("Bookings per day", "bar", {"graph.dimensions": ["day"], "graph.metrics": ["bookings"]}, 9, 6, f"""
select date_trunc('day', created_at at time zone '{TZ}')::date as day, count(*) as bookings
from bookings group by 1 order by 1"""),
    ("Bookings by service", "pie", {"pie.dimension": "service", "pie.metric": "bookings"}, 9, 6, """
select service, count(*) as bookings from bookings group by 1 order by 2 desc"""),
    ("Calls", "table", {}, 18, 9, f"""
select c.started_at at time zone '{TZ}' as started, c.channel, c.phone,
       round(extract(epoch from c.ended_at - c.started_at))::int as seconds,
       (select count(*) from call_turns t where t.call_id = c.id and t.role = 'caller') as caller_turns,
       exists (select 1 from bookings b where b.call_id = c.id) as booked, c.id as call_id
from calls c order by c.started_at desc"""),
    ("Call transcripts", "table", {}, 18, 10, f"""
select t.at at time zone '{TZ}' as at, t.call_id, t.role, t.content, t.interrupted
from call_turns t join calls c on c.id = t.call_id
order by c.started_at desc, t.id"""),
]

dash_name = "Bookings"
existing = [d for d in api("GET", "/api/dashboard", session=session) if d["name"] == dash_name]
if existing:
    print("Dashboard already exists:", existing[0]["id"])
else:
    dashcards, row, col = [], 0, 0
    for i, (name, display, viz, w, h, sql) in enumerate(CARDS):
        card = api("POST", "/api/card", {
            "name": name, "display": display, "visualization_settings": viz,
            "dataset_query": {"type": "native", "database": db_id, "native": {"query": sql.strip()}},
        }, session=session)
        if col + w > 18:
            row, col = row + h, 0
        dashcards.append({"id": -(i + 1), "card_id": card["id"], "row": row, "col": col, "size_x": w, "size_y": h})
        col += w
        if col >= 18:
            row, col = row + h, 0
    dash = api("POST", "/api/dashboard", {"name": dash_name}, session=session)
    api("PUT", f"/api/dashboard/{dash['id']}", {"dashcards": dashcards}, session=session)
    print("Dashboard created:", dash["id"])
