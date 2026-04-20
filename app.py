from __future__ import annotations

import datetime as dt
import json
import math
import os
import sqlite3
import urllib.parse
import urllib.request
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"


def resolve_db_path() -> Path:
    explicit = os.environ.get("DB_PATH", "").strip()
    if explicit:
        return Path(explicit)
    railway_volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if railway_volume:
        return Path(railway_volume) / "data.db"
    return BASE_DIR / "data.db"


DB_PATH = resolve_db_path()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["JSON_AS_ASCII"] = False

TEAM_PASSWORD = os.environ.get("TEAM_PASSWORD", "")
APP_TITLE = os.environ.get("APP_TITLE", "Planificador de Rutas v3.1")
APP_BUILD = os.environ.get("APP_BUILD", "20260420-railway-fix")

CATEGORY_META = {
    "servicio": {"label": "Llamada de servicio", "color": "#ef4444"},
    "proyecto": {"label": "Proyecto", "color": "#2563eb"},
    "calidad": {"label": "Calidad", "color": "#f59e0b"},
}

SEGMENT_COLORS = [
    "#2563eb",
    "#10b981",
    "#f97316",
    "#a855f7",
    "#e11d48",
    "#0ea5e9",
    "#84cc16",
    "#f59e0b",
]

SORT_FIELDS = {
    "po": "po",
    "localidad": "localidad",
    "direccion": "direccion",
    "due_date": "due_date",
    "tipo": "type",
    "tag": "tag",
    "selected": "selected",
    "updated_at": "updated_at",
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            direccion TEXT NOT NULL,
            geocoded_address TEXT,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            po TEXT NOT NULL,
            localidad TEXT,
            direccion TEXT NOT NULL,
            geocoded_address TEXT,
            external_link TEXT,
            type TEXT NOT NULL,
            tag TEXT,
            color TEXT,
            due_date TEXT,
            selected INTEGER NOT NULL DEFAULT 0,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            created_by TEXT,
            updated_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


init_db()


def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if TEAM_PASSWORD and not session.get("user_name"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "No autorizado"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def row_to_base(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "direccion": row["direccion"],
        "geocoded_address": row["geocoded_address"] or "",
        "lat": row["lat"],
        "lng": row["lng"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_stop(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "po": row["po"],
        "localidad": row["localidad"] or "",
        "direccion": row["direccion"],
        "geocoded_address": row["geocoded_address"] or "",
        "external_link": row["external_link"] or "",
        "type": row["type"],
        "tag": row["tag"] or "",
        "color": row["color"] or CATEGORY_META.get(row["type"], CATEGORY_META["proyecto"])["color"],
        "due_date": row["due_date"] or "",
        "selected": bool(row["selected"]),
        "lat": row["lat"],
        "lng": row["lng"],
        "created_by": row["created_by"] or "",
        "updated_by": row["updated_by"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_all_bases() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM bases ORDER BY is_active DESC, name COLLATE NOCASE ASC").fetchall()
    conn.close()
    return [row_to_base(r) for r in rows]


def get_all_stops(sort_by: str = "updated_at", direction: str = "desc") -> list[dict[str, Any]]:
    sort_col = SORT_FIELDS.get(sort_by, "updated_at")
    sort_dir = "ASC" if direction.lower() == "asc" else "DESC"
    conn = get_conn()
    rows = conn.execute(
        f"SELECT * FROM stops ORDER BY {sort_col} {sort_dir}, id DESC"
    ).fetchall()
    conn.close()
    return [row_to_stop(r) for r in rows]


def geocode_address(query: str) -> dict[str, Any]:
    url = (
        "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&addressdetails=1&q="
        + urllib.parse.quote(query)
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "planificador-rutas-v3/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        data = json.loads(res.read().decode("utf-8"))
    if not data:
        raise ValueError("No encontré esa dirección. Pégala más completa, como aparece en Google Maps.")
    item = data[0]
    address = item.get("address", {})
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or address.get("municipality")
        or ""
    )
    state = address.get("state") or address.get("region") or ""
    country = address.get("country") or ""
    return {
        "lat": float(item["lat"]),
        "lng": float(item["lon"]),
        "display_name": item.get("display_name") or query,
        "city": city,
        "state": state,
        "country": country,
    }


def haversine_m(a: dict[str, float], b: dict[str, float]) -> float:
    r = 6371000
    dlat = math.radians(b["lat"] - a["lat"])
    dlng = math.radians(b["lng"] - a["lng"])
    lat1 = math.radians(a["lat"])
    lat2 = math.radians(b["lat"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def osrm_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "planificador-rutas-v3/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=35) as res:
        return json.loads(res.read().decode("utf-8"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not TEAM_PASSWORD:
        session["user_name"] = session.get("user_name") or "Equipo"
        return redirect(url_for("index"))
    error = ""
    if request.method == "POST":
        user_name = (request.form.get("user_name") or "").strip()
        password = request.form.get("password") or ""
        if not user_name:
            error = "Escribe tu nombre."
        elif password != TEAM_PASSWORD:
            error = "Contraseña incorrecta."
        else:
            session["user_name"] = user_name
            return redirect(url_for("index"))
    return render_template("login.html", title=APP_TITLE, error=error)


@app.post("/logout")
@require_login
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@require_login
def index():
    return render_template(
        "index.html",
        title=APP_TITLE,
        build=APP_BUILD,
        user_name=session.get("user_name", "Equipo"),
        team_password_enabled=bool(TEAM_PASSWORD),
        category_meta=CATEGORY_META,
        segment_colors=SEGMENT_COLORS,
    )


@app.get("/health")
def health():
    return "OK", 200


@app.get("/api/bootstrap")
@require_login
def api_bootstrap():
    sort_by = request.args.get("sort_by", "updated_at")
    direction = request.args.get("direction", "desc")
    return jsonify(
        {
            "ok": True,
            "app": {
                "title": APP_TITLE,
                "build": APP_BUILD,
                "user_name": session.get("user_name", "Equipo"),
                "category_meta": CATEGORY_META,
                "segment_colors": SEGMENT_COLORS,
            },
            "bases": get_all_bases(),
            "stops": get_all_stops(sort_by, direction),
        }
    )


@app.post("/api/geocode")
@require_login
def api_geocode():
    data = request.get_json(force=True, silent=True) or {}
    address = (data.get("address") or "").strip()
    if not address:
        return jsonify({"ok": False, "error": "La dirección es obligatoria."}), 400
    try:
        result = geocode_address(address)
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/bases")
@require_login
def api_bases():
    return jsonify({"ok": True, "bases": get_all_bases()})


@app.post("/api/bases")
@require_login
def api_create_base():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    direccion = (data.get("direccion") or "").strip()
    if not name or not direccion:
        return jsonify({"ok": False, "error": "Nombre y dirección son obligatorios."}), 400
    try:
        resolved = geocode_address(direccion)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    created = now_iso()
    conn = get_conn()
    active_count = conn.execute("SELECT COUNT(*) FROM bases WHERE is_active = 1").fetchone()[0]
    conn.execute(
        """
        INSERT INTO bases (name, direccion, geocoded_address, lat, lng, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, direccion, resolved["display_name"], resolved["lat"], resolved["lng"], 0 if active_count else 1, created, created),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.put("/api/bases/<int:base_id>")
@require_login
def api_update_base(base_id: int):
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    direccion = (data.get("direccion") or "").strip()
    if not name or not direccion:
        return jsonify({"ok": False, "error": "Nombre y dirección son obligatorios."}), 400
    try:
        resolved = geocode_address(direccion)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    conn = get_conn()
    conn.execute(
        """
        UPDATE bases
        SET name = ?, direccion = ?, geocoded_address = ?, lat = ?, lng = ?, updated_at = ?
        WHERE id = ?
        """,
        (name, direccion, resolved["display_name"], resolved["lat"], resolved["lng"], now_iso(), base_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/bases/<int:base_id>/active")
@require_login
def api_set_active_base(base_id: int):
    conn = get_conn()
    conn.execute("UPDATE bases SET is_active = 0")
    conn.execute("UPDATE bases SET is_active = 1, updated_at = ? WHERE id = ?", (now_iso(), base_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.delete("/api/bases/<int:base_id>")
@require_login
def api_delete_base(base_id: int):
    conn = get_conn()
    was_active = conn.execute("SELECT is_active FROM bases WHERE id = ?", (base_id,)).fetchone()
    conn.execute("DELETE FROM bases WHERE id = ?", (base_id,))
    conn.commit()
    if was_active and was_active[0]:
        first = conn.execute("SELECT id FROM bases ORDER BY name COLLATE NOCASE LIMIT 1").fetchone()
        if first:
            conn.execute("UPDATE bases SET is_active = 1, updated_at = ? WHERE id = ?", (now_iso(), first[0]))
            conn.commit()
    conn.close()
    return jsonify({"ok": True})


def validate_stop_payload(data: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    po = (data.get("po") or "").strip()
    direccion = (data.get("direccion") or "").strip()
    localidad = (data.get("localidad") or "").strip()
    external_link = (data.get("external_link") or "").strip()
    type_ = (data.get("type") or "proyecto").strip()
    tag = (data.get("tag") or "").strip()
    color = (data.get("color") or CATEGORY_META.get(type_, CATEGORY_META["proyecto"])["color"]).strip()
    due_date = (data.get("due_date") or "").strip()
    selected = 1 if data.get("selected") else 0
    if not po:
        return None, "El PO# es obligatorio."
    if not direccion:
        return None, "La dirección es obligatoria."
    if type_ not in CATEGORY_META:
        type_ = "proyecto"
    if due_date:
        try:
            dt.date.fromisoformat(due_date)
        except ValueError:
            return None, "El due date debe venir en formato YYYY-MM-DD."
    try:
        resolved = geocode_address(direccion)
    except Exception as exc:
        return None, str(exc)
    if not localidad:
        localidad = ", ".join([p for p in [resolved["city"], resolved["state"]] if p]) or resolved["country"] or "Sin localidad"
    payload = {
        "po": po,
        "localidad": localidad,
        "direccion": direccion,
        "geocoded_address": resolved["display_name"],
        "external_link": external_link,
        "type": type_,
        "tag": tag,
        "color": color,
        "due_date": due_date,
        "selected": selected,
        "lat": resolved["lat"],
        "lng": resolved["lng"],
    }
    return payload, None


@app.get("/api/stops")
@require_login
def api_stops():
    sort_by = request.args.get("sort_by", "updated_at")
    direction = request.args.get("direction", "desc")
    return jsonify({"ok": True, "stops": get_all_stops(sort_by, direction)})


@app.post("/api/stops")
@require_login
def api_create_stop():
    data = request.get_json(force=True, silent=True) or {}
    payload, error = validate_stop_payload(data)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    stamp = now_iso()
    user_name = session.get("user_name", "")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO stops (
          po, localidad, direccion, geocoded_address, external_link, type, tag, color,
          due_date, selected, lat, lng, created_by, updated_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["po"], payload["localidad"], payload["direccion"], payload["geocoded_address"],
            payload["external_link"], payload["type"], payload["tag"], payload["color"], payload["due_date"],
            payload["selected"], payload["lat"], payload["lng"], user_name, user_name, stamp, stamp,
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.put("/api/stops/<int:stop_id>")
@require_login
def api_update_stop(stop_id: int):
    data = request.get_json(force=True, silent=True) or {}
    payload, error = validate_stop_payload(data)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    user_name = session.get("user_name", "")
    conn = get_conn()
    conn.execute(
        """
        UPDATE stops
        SET po = ?, localidad = ?, direccion = ?, geocoded_address = ?, external_link = ?,
            type = ?, tag = ?, color = ?, due_date = ?, selected = ?, lat = ?, lng = ?,
            updated_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload["po"], payload["localidad"], payload["direccion"], payload["geocoded_address"],
            payload["external_link"], payload["type"], payload["tag"], payload["color"], payload["due_date"],
            payload["selected"], payload["lat"], payload["lng"], user_name, now_iso(), stop_id,
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.patch("/api/stops/<int:stop_id>")
@require_login
def api_patch_stop(stop_id: int):
    data = request.get_json(force=True, silent=True) or {}
    fields = []
    values: list[Any] = []
    if "selected" in data:
        fields.append("selected = ?")
        values.append(1 if data.get("selected") else 0)
    if "due_date" in data:
        dd = (data.get("due_date") or "").strip()
        if dd:
            try:
                dt.date.fromisoformat(dd)
            except ValueError:
                return jsonify({"ok": False, "error": "Due date inválido."}), 400
        fields.append("due_date = ?")
        values.append(dd)
    if not fields:
        return jsonify({"ok": False, "error": "No hay cambios."}), 400
    fields.append("updated_by = ?")
    values.append(session.get("user_name", ""))
    fields.append("updated_at = ?")
    values.append(now_iso())
    values.append(stop_id)
    conn = get_conn()
    conn.execute(f"UPDATE stops SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.delete("/api/stops/<int:stop_id>")
@require_login
def api_delete_stop(stop_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM stops WHERE id = ?", (stop_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/stops/bulk-selection")
@require_login
def api_bulk_selection():
    data = request.get_json(force=True, silent=True) or {}
    ids = [int(x) for x in data.get("ids", []) if str(x).isdigit()]
    value = 1 if data.get("selected") else 0
    if not ids:
        return jsonify({"ok": False, "error": "No hay ids."}), 400
    marks = ",".join("?" for _ in ids)
    conn = get_conn()
    conn.execute(
        f"UPDATE stops SET selected = ?, updated_by = ?, updated_at = ? WHERE id IN ({marks})",
        [value, session.get("user_name", ""), now_iso(), *ids],
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/route")
@require_login
def api_route():
    data = request.get_json(force=True, silent=True) or {}
    selected_ids = [int(x) for x in data.get("selected_ids", []) if str(x).isdigit()]
    base_id = data.get("base_id")
    if not selected_ids:
        return jsonify({"ok": False, "error": "No hay paradas seleccionadas."}), 400
    conn = get_conn()
    marks = ",".join("?" for _ in selected_ids)
    stop_rows = conn.execute(f"SELECT * FROM stops WHERE id IN ({marks})", selected_ids).fetchall()
    base_row = conn.execute("SELECT * FROM bases WHERE id = ?", (base_id,)).fetchone() if base_id else None
    conn.close()
    stops = [row_to_stop(r) for r in stop_rows]
    base = row_to_base(base_row) if base_row else None
    if (not base and len(stops) < 2) or (base and len(stops) < 1):
        return jsonify({"ok": False, "error": "Paradas insuficientes para calcular la ruta."}), 400

    input_points: list[dict[str, Any]] = []
    if base:
        input_points.append({"kind": "base", "id": base["id"], "lat": base["lat"], "lng": base["lng"], "name": base["name"]})
    for stop in stops:
        input_points.append({"kind": "stop", "id": stop["id"], "lat": stop["lat"], "lng": stop["lng"], "name": stop["localidad"], "color": stop["color"]})

    coord_string = ";".join(f"{p['lng']},{p['lat']}" for p in input_points)
    trip_url = f"https://router.project-osrm.org/trip/v1/driving/{coord_string}?source=first&roundtrip=false&steps=false&overview=false"
    try:
        trip = osrm_json(trip_url)
        if trip.get("code") != "Ok" or not trip.get("trips"):
            raise RuntimeError("Sin respuesta válida del optimizador")
        ordered_items = [
            item for _, item in sorted(
                ((wp["waypoint_index"], input_points[i]) for i, wp in enumerate(trip["waypoints"])),
                key=lambda x: x[0],
            )
        ]
    except Exception:
        remaining = stops[:]
        ordered_items = []
        current = base or remaining.pop(0)
        if base:
            ordered_items.append({"kind": "base", "id": base["id"], "lat": base["lat"], "lng": base["lng"], "name": base["name"]})
        else:
            ordered_items.append({"kind": "stop", "id": current["id"], "lat": current["lat"], "lng": current["lng"], "name": current["localidad"], "color": current["color"]})
        while remaining:
            ref = {"lat": current["lat"], "lng": current["lng"]}
            next_idx = min(range(len(remaining)), key=lambda idx: haversine_m(ref, remaining[idx]))
            current = remaining.pop(next_idx)
            ordered_items.append({"kind": "stop", "id": current["id"], "lat": current["lat"], "lng": current["lng"], "name": current["localidad"], "color": current["color"]})

    ordered_stops = [item for item in ordered_items if item["kind"] == "stop"]
    segments = []
    total_distance = 0.0
    total_duration = 0.0

    for i in range(len(ordered_items) - 1):
        a = ordered_items[i]
        b = ordered_items[i + 1]
        seg_color = SEGMENT_COLORS[i % len(SEGMENT_COLORS)]
        url = (
            f"https://router.project-osrm.org/route/v1/driving/{a['lng']},{a['lat']};{b['lng']},{b['lat']}"
            "?overview=full&geometries=geojson&steps=false"
        )
        try:
            route = osrm_json(url)
            if route.get("code") != "Ok" or not route.get("routes"):
                raise RuntimeError("sin ruta")
            r = route["routes"][0]
            geometry = [[lat, lng] for lng, lat in r["geometry"]["coordinates"]]
            distance = float(r.get("distance", 0))
            duration = float(r.get("duration", 0))
        except Exception:
            geometry = [[a["lat"], a["lng"]], [b["lat"], b["lng"]]]
            distance = haversine_m(a, b)
            duration = 0.0
        total_distance += distance
        total_duration += duration
        mid = geometry[len(geometry) // 2]
        segments.append(
            {
                "from": {"kind": a["kind"], "id": a["id"], "name": a["name"]},
                "to": {"kind": b["kind"], "id": b["id"], "name": b["name"]},
                "geometry": geometry,
                "distance_m": distance,
                "duration_s": duration,
                "color": seg_color,
                "arrow_at": mid,
            }
        )

    return jsonify(
        {
            "ok": True,
            "route": {
                "base_id": base["id"] if base else None,
                "order_ids": [s["id"] for s in ordered_stops],
                "distance_m": total_distance,
                "duration_s": total_duration,
                "segments": segments,
            },
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=True)
