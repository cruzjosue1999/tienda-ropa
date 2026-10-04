#!/usr/bin/env python3
"""
Tienda de ropa - sistema completo.
- GET /        -> tienda pública para clientes (PWA instalable)
- GET /admin   -> app privada de administración (PWA instalable, con contraseña)
Toda la interfaz en español, mobile-first.
"""
import os
import io
import json
import sqlite3
import secrets
import uuid
import time
from datetime import timedelta
from functools import wraps

from flask import (
    Flask, request, jsonify, redirect, session,
    send_from_directory, render_template, g, Response,
)
from werkzeug.security import generate_password_hash, check_password_hash

import segno
import stripe

try:
    import whatsapp_cloud
except ImportError:  # el módulo es opcional: sin él no hay avisos automáticos
    whatsapp_cloud = None

try:
    from pywebpush import webpush as _pywebpush_send, WebPushException
    _HAS_PYWEBPUSH = True
except ImportError:  # sin pywebpush no hay notificaciones push del admin
    _pywebpush_send = None
    WebPushException = None
    _HAS_PYWEBPUSH = False

try:
    import libsql
    _HAS_LIBSQL = True
except ImportError:  # pragma: no cover
    libsql = None
    _HAS_LIBSQL = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "tienda.db")
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB por foto

# Clave secreta de sesión (persistente entre reinicios)
SECRET_FILE = os.path.join(DATA_DIR, ".flask_secret")
if os.path.exists(SECRET_FILE):
    with open(SECRET_FILE) as f:
        app.secret_key = f.read().strip()
else:
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    with open(SECRET_FILE, "w") as f:
        f.write(app.secret_key)
    os.chmod(SECRET_FILE, 0o600)

# La sesión del admin persiste en el teléfono (no pide la contraseña cada vez).
app.permanent_session_lifetime = timedelta(days=365)

ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
MAGIC_BYTES = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",  # se valida WEBP después
}


# ---------------- Base de datos ----------------
# En Render (plan gratuito) el disco es temporal: cada despliegue borra el
# archivo SQLite local. Si existen TURSO_URL y TURSO_TOKEN, la app usa Turso
# (réplica embebida libsql, compatible con SQLite) y los datos sobreviven a
# los despliegues. Sin esas variables, usa SQLite local como antes.


class _Row:
    """Fila compatible con sqlite3.Row: acceso por índice y por nombre."""
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = tuple(vals)

    def __getitem__(self, key):
        if isinstance(key, str):
            key = self._cols.index(key)
        return self._vals[key]

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)


class _Cursor:
    def __init__(self, cur):
        self._cur = cur
        self._cols = [d[0] for d in (cur.description or [])]

    def _wrap(self, row):
        return _Row(self._cols, row) if row is not None else None

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]

    def __iter__(self):
        # No iterar el cursor crudo directamente: el Cursor de libsql
        # (producción) no es iterable, a diferencia del de sqlite3.
        for r in self._cur.fetchall():
            yield self._wrap(r)

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount


class _TursoConn:
    """Conexión libsql con la misma interfaz que la app espera de sqlite3."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return _Cursor(self._conn.execute(sql, params))

    def executemany(self, sql, seq):
        return self._conn.executemany(sql, seq)

    def executescript(self, sql):
        return self._conn.executescript(sql)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()

    def sync(self):
        return self._conn.sync()


def _turso_sync_url():
    """URL de sincronización normalizada (libsql:// -> https://)."""
    url = (os.environ.get("TURSO_URL") or "").strip().rstrip("/")
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return url


def _turso_enabled():
    return (
        _HAS_LIBSQL
        and bool(_turso_sync_url())
        and bool(os.environ.get("TURSO_TOKEN"))
    )


def _connect_db():
    if _turso_enabled():
        try:
            conn = libsql.connect(
                DB_PATH,
                sync_url=_turso_sync_url(),
                auth_token=os.environ["TURSO_TOKEN"],
            )
            wrapped = _TursoConn(conn)
            try:
                wrapped.sync()  # traer lo último de la nube
            except Exception as e:
                print(f"[DB] Turso sync inicial falló: {e}", flush=True)
            return wrapped
        except Exception as e:
            print(f"[DB] No se pudo conectar a Turso, usando SQLite local: {e}", flush=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect_db()
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            if isinstance(db, _TursoConn):
                db.sync()  # subir los cambios a la nube
        except Exception as e:
            print(f"[DB] Turso sync final falló: {e}", flush=True)
        db.close()


def init_db():
    print(
        "[DB] modo=%s libsql=%s TURSO_URL=%s TURSO_TOKEN=%s"
        % (
            "turso" if _turso_enabled() else "local",
            _HAS_LIBSQL,
            "definida" if os.environ.get("TURSO_URL") else "ausente",
            "definido" if os.environ.get("TURSO_TOKEN") else "ausente",
        ),
        flush=True,
    )
    db = _connect_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            price_cents INTEGER NOT NULL DEFAULT 0,
            sizes TEXT DEFAULT '[]',
            sku TEXT DEFAULT '',
            stock INTEGER NOT NULL DEFAULT 0,
            photo TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            items TEXT NOT NULL,
            total_cents INTEGER NOT NULL,
            stripe_session_id TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            shipping_cents INTEGER DEFAULT 0,
            auto_paid INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS pending_uploads (
            id TEXT PRIMARY KEY,
            data BLOB NOT NULL,
            mime TEXT DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS product_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            data BLOB NOT NULL,
            mime TEXT DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_product_photos_pid
            ON product_photos(product_id);
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    # Migración de moneda: la tienda ahora usa lempiras (HNL).
    # Si la BD ya tenía "usd" guardado, se cambia a "hnl" automáticamente.
    row = db.execute("SELECT value FROM settings WHERE key='currency'").fetchone()
    if row and (row[0] or "").strip().lower() == "usd":
        db.execute("UPDATE settings SET value='hnl' WHERE key='currency'")
    # Migración de marca: la tienda ahora se llama "Tu Nuevo Estilo".
    # Solo renombra si el valor guardado es uno de los nombres anteriores,
    # para no pisar un nombre que el dueño haya personalizado.
    row = db.execute("SELECT value FROM settings WHERE key='store_name'").fetchone()
    if row and (row[0] or "").strip() in (
        "Mi Tienda de Ropa",
        "Mi Tienda",
        "US Style Honduras",
    ):
        db.execute("UPDATE settings SET value='Tu Nuevo Estilo' WHERE key='store_name'")
    # Migración: columna category en products (para filtrar por categorías).
    cols = [c[1] for c in db.execute("PRAGMA table_info(products)").fetchall()]
    if "category" not in cols:
        db.execute("ALTER TABLE products ADD COLUMN category TEXT DEFAULT ''")
    # Migración (2026-09-30): costo del producto en lempiras, para las
    # gráficas de ganancias del admin. Queda en 0 hasta que el dueño lo
    # ingrese editando cada producto; eso es esperado, no inventar costos.
    if "cost" not in cols:
        db.execute("ALTER TABLE products ADD COLUMN cost REAL DEFAULT 0")
    # Migración: marca qué pedidos fueron confirmados por pago automático
    # (tarjeta/cuenta vía pasarela). Esos no se pueden borrar ni revertir.
    ocols_auto = [c[1] for c in db.execute("PRAGMA table_info(orders)").fetchall()]
    if "auto_paid" not in ocols_auto:
        db.execute("ALTER TABLE orders ADD COLUMN auto_paid INTEGER NOT NULL DEFAULT 0")
    # Migración: fotos persistentes como BLOB (sobreviven a los despliegues).
    if "photo_blob" not in cols:
        db.execute("ALTER TABLE products ADD COLUMN photo_blob BLOB")
    if "photo_mime" not in cols:
        db.execute("ALTER TABLE products ADD COLUMN photo_mime TEXT DEFAULT ''")
    # Migración: datos de entrega en orders (nombre, dirección, ciudad,
    # departamento y método de entrega).
    ocols = [c[1] for c in db.execute("PRAGMA table_info(orders)").fetchall()]
    for col in (
        "customer_name",
        "customer_address",
        "customer_city",
        "customer_department",
        "delivery_method",
    ):
        if col not in ocols:
            db.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT DEFAULT ''")
    # Migración: costo de envío guardado en el pedido.
    if "shipping_cents" not in ocols:
        db.execute("ALTER TABLE orders ADD COLUMN shipping_cents INTEGER DEFAULT 0")
    # Migración: forma de pago elegida por el cliente (efectivo / deposito).
    if "payment_method" not in ocols:
        db.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT ''")
    # Migración fase 1 (2026-09-30): teléfono, identificación, opt-in de
    # WhatsApp, estado de cumplimiento del pedido y número de guía.
    if "customer_phone" not in ocols:
        db.execute("ALTER TABLE orders ADD COLUMN customer_phone TEXT DEFAULT ''")
    if "customer_id_number" not in ocols:
        db.execute("ALTER TABLE orders ADD COLUMN customer_id_number TEXT DEFAULT ''")
    if "whatsapp_optin" not in ocols:
        db.execute(
            "ALTER TABLE orders ADD COLUMN whatsapp_optin INTEGER NOT NULL DEFAULT 0"
        )
    if "fulfillment_status" not in ocols:
        db.execute(
            "ALTER TABLE orders ADD COLUMN fulfillment_status TEXT NOT NULL DEFAULT 'pending'"
        )
    if "tracking_number" not in ocols:
        db.execute("ALTER TABLE orders ADD COLUMN tracking_number TEXT DEFAULT ''")
    # Migración (2026-09-30): persona autorizada a recibir el pedido a
    # domicilio (campo opcional que el cliente puede indicar en el checkout).
    if "authorized_receiver" not in ocols:
        db.execute("ALTER TABLE orders ADD COLUMN authorized_receiver TEXT DEFAULT ''")
    db.commit()
    try:
        if isinstance(db, _TursoConn):
            db.sync()  # propagar el esquema a la nube
    except Exception:
        pass
    db.close()


def get_setting(key, default=""):
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db.commit()


def _gallery_items(db, pid):
    """Fotos de la galería de un producto (sin la portada)."""
    rows = db.execute(
        "SELECT id FROM product_photos WHERE product_id=? ORDER BY position, id",
        (pid,),
    ).fetchall()
    return [{"id": r["id"], "url": f"/api/product-photo/{r['id']}"} for r in rows]


def _gallery_map(db, pids):
    """Galería de varios productos en una sola consulta."""
    m = {pid: [] for pid in pids}
    if not pids:
        return m
    q = ",".join("?" for _ in pids)
    rows = db.execute(
        f"SELECT id, product_id FROM product_photos WHERE product_id IN ({q}) "
        "ORDER BY position, id",
        list(pids),
    ).fetchall()
    for r in rows:
        m[r["product_id"]].append(
            {"id": r["id"], "url": f"/api/product-photo/{r['id']}"}
        )
    return m


def _photo_state(db, pid):
    """Portada + galería actuales de un producto (para el admin)."""
    row = db.execute("SELECT photo FROM products WHERE id=?", (pid,)).fetchone()
    return {
        "photo": (row["photo"] or "") if row else "",
        "photo_items": _gallery_items(db, pid),
    }


def product_to_dict(row, gallery=None, include_cost=False):
    """Convierte una fila de producto a dict. El costo solo se incluye cuando
    lo pide el admin (include_cost=True); el endpoint público nunca lo expone."""
    photo = row["photo"] or ""
    items = gallery or []
    photos = ([photo] if photo else []) + [it["url"] for it in items]
    d = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "price_cents": row["price_cents"],
        "sizes": json.loads(row["sizes"] or "[]"),
        "sku": row["sku"] or "",
        "stock": row["stock"],
        "photo": photo,
        "photos": photos,
        "photo_items": items,
        "active": bool(row["active"]),
        "category": row["category"] or "",
        "created_at": row["created_at"],
    }
    if include_cost:
        d["cost_cents"] = _pcol_cost(row)
    return d


def _pcol_cost(row):
    """Costo del producto en centavos, tolerando BDs viejas sin la columna."""
    try:
        v = row["cost"]
    except (KeyError, ValueError, IndexError):
        return 0
    try:
        return int(round(float(v or 0) * 100))
    except (TypeError, ValueError):
        return 0


def admin_password_set():
    return bool(get_setting("admin_password_hash"))


def login_required(view):
    # El dueño pidió entrar al admin sin contraseña (2026-09-30):
    # cualquiera con el enlace puede administrar la tienda.
    @wraps(view)
    def wrapped(*args, **kwargs):
        return view(*args, **kwargs)

    return wrapped


# ---------------- Páginas ----------------
@app.route("/")
def store():
    return render_template(
        "store.html",
        store_name=get_setting("store_name", "Tu Nuevo Estilo"),
    )


@app.route("/exito")
def success():
    return render_template("success.html")


@app.route("/cancelado")
def cancelled():
    return render_template("cancel.html")


@app.route("/admin")
def admin_index():
    # Sin contraseña por decisión del dueño (2026-09-30): entra directo.
    return render_template("admin.html")


@app.route("/admin/login")
def admin_login_page():
    if not admin_password_set():
        return redirect("/admin/setup")
    if session.get("admin"):
        return redirect("/admin")
    return render_template("admin_login.html")


@app.route("/admin/setup")
def admin_setup_page():
    if admin_password_set():
        return redirect("/admin/login")
    return render_template("admin_setup.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin")


# PWA: manifest dinámico (usa el nombre de la tienda)
@app.route("/manifest.json")
def manifest():
    store_name = get_setting("store_name", "Tu Nuevo Estilo")
    return jsonify(
        {
            "name": store_name,
            "short_name": store_name,
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#ffffff",
            "theme_color": "#111827",
            "lang": "es",
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {
                    "src": "/static/icon-maskable-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
    )


@app.route("/admin/manifest.json")
def admin_manifest():
    return jsonify(
        {
            "name": "Tu Nuevo Estilo Admin",
            "short_name": "Admin",
            "start_url": "/admin",
            "scope": "/admin",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#111827",
            "theme_color": "#111827",
            "lang": "es",
            "icons": [
                {
                    "src": "/static/icon-admin-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                },
                {
                    "src": "/static/icon-admin-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                },
                {
                    "src": "/static/icon-admin-maskable-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
    )


# Android (Google Play / TWA): verificación Digital Asset Links.
# Después de subir el .aab a Play Console, copia la huella SHA-256 de la
# "clave de firma de la app" y pégala en ASSETLINK_FINGERPRINTS.
ASSETLINK_FINGERPRINTS = [
    # "AA:BB:CC:DD:...",
]


@app.route("/.well-known/assetlinks.json")
def assetlinks():
    return jsonify(
        [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": "com.tiendaropa.app",
                    "sha256_cert_fingerprints": ASSETLINK_FINGERPRINTS,
                },
            },
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": "com.tiendaropa.admin",
                    "sha256_cert_fingerprints": ASSETLINK_FINGERPRINTS,
                },
            },
        ]
    )


@app.route("/sw.js")
def sw():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/admin/sw.js")
def admin_sw():
    return send_from_directory(
        "static", "admin-sw.js", mimetype="application/javascript"
    )


@app.route("/apple-touch-icon.png")
def apple_icon():
    return send_from_directory("static", "apple-touch-icon.png")


# ---------------- Auth API ----------------
@app.route("/api/setup", methods=["POST"])
def api_setup():
    """Crea la contraseña de admin. Solo permitido si aún no existe."""
    if admin_password_set():
        return jsonify({"error": "La contraseña ya fue creada."}), 403
    data = request.get_json(force=True, silent=True) or {}
    password = (data.get("password") or "").strip()
    if len(password) < 8:
        return jsonify({"error": "La contraseña debe tener al menos 8 caracteres."}), 400
    set_setting("admin_password_hash", generate_password_hash(password))
    session["admin"] = True
    session.permanent = True
    return jsonify({"ok": True})


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password") or ""
    if admin_password_set() and check_password_hash(
        get_setting("admin_password_hash"), password
    ):
        session["admin"] = True
        session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"error": "Contraseña incorrecta."}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.route("/api/admin/me")
@login_required
def api_me():
    return jsonify({"ok": True, "store_name": get_setting("store_name", "Tu Nuevo Estilo")})


@app.route("/api/admin/change-password", methods=["POST"])
@login_required
def api_change_password():
    data = request.get_json(force=True, silent=True) or {}
    current = data.get("current") or ""
    new = (data.get("new") or "").strip()
    if not check_password_hash(get_setting("admin_password_hash"), current):
        return jsonify({"error": "La contraseña actual no es correcta."}), 400
    if len(new) < 8:
        return jsonify({"error": "La nueva contraseña debe tener al menos 8 caracteres."}), 400
    set_setting("admin_password_hash", generate_password_hash(new))
    return jsonify({"ok": True})


# ---------------- API pública ----------------
@app.route("/api/products")
def api_products():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM products WHERE active=1 ORDER BY created_at DESC"
    ).fetchall()
    gmap = _gallery_map(db, [r["id"] for r in rows])
    return jsonify([product_to_dict(r, gmap[r["id"]]) for r in rows])


@app.route("/rastrear")
def track_page():
    return render_template(
        "track.html",
        store_name=get_setting("store_name", "Tu Nuevo Estilo"),
    )


@app.route("/api/track")
def api_track():
    """El cliente rastrea su pedido con el número de orden + su celular.

    Privacidad: solo devuelve datos si el teléfono coincide con el del pedido;
    en cualquier otro caso responde 404 (igual que si no existiera).
    """
    try:
        order_id = int(request.args.get("order", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "Pedido no encontrado."}), 404
    phone = norm_phone(request.args.get("phone", ""))
    if not phone:
        return jsonify({"error": "Pedido no encontrado."}), 404
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row or norm_phone(_order_col(row, "customer_phone", "")) != phone:
        return jsonify({"error": "Pedido no encontrado."}), 404
    return jsonify(
        {
            "id": row["id"],
            "payment_status": row["status"],
            "fulfillment_status": _order_col(row, "fulfillment_status", "pending") or "pending",
            "tracking_number": _order_col(row, "tracking_number", ""),
            "created_at": row["created_at"],
        }
    )


@app.route("/api/order-status")
def api_order_status():
    """El cliente consulta el estado de su pedido tras volver de Stripe."""
    session_id = request.args.get("session_id", "")
    if not session_id:
        return jsonify({"error": "Falta session_id."}), 400
    db = get_db()
    row = db.execute(
        "SELECT * FROM orders WHERE stripe_session_id=?", (session_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Pedido no encontrado."}), 404
    return jsonify(
        {"id": row["id"], "status": row["status"], "total_cents": row["total_cents"]}
    )


# ---------------- API admin: productos ----------------
@app.route("/api/admin/products")
@login_required
def admin_list_products():
    db = get_db()
    rows = db.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    gmap = _gallery_map(db, [r["id"] for r in rows])
    return jsonify([product_to_dict(r, gmap[r["id"]], include_cost=True) for r in rows])


def parse_product_input(data):
    sizes = data.get("sizes", [])
    if isinstance(sizes, str):
        sizes = [s.strip() for s in sizes.split(",") if s.strip()]
    try:
        price_cents = int(round(float(data.get("price", 0)) * 100))
    except (TypeError, ValueError):
        price_cents = 0
    try:
        stock = int(data.get("stock", 0))
    except (TypeError, ValueError):
        stock = 0
    # El costo llega en lempiras ("cost"); también se acepta "cost_cents"
    # para llamadas internas que ya lo traen calculado (ej: edición rápida).
    cost_cents = 0
    if "cost" in data:
        try:
            cost_cents = int(round(float(data.get("cost") or 0) * 100))
        except (TypeError, ValueError):
            cost_cents = 0
    elif "cost_cents" in data:
        try:
            cost_cents = int(data.get("cost_cents") or 0)
        except (TypeError, ValueError):
            cost_cents = 0
    return {
        "name": (data.get("name") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "price_cents": max(0, price_cents),
        "cost_cents": max(0, cost_cents),
        "sizes": json.dumps(sizes),
        "sku": (data.get("sku") or "").strip(),
        "stock": max(0, stock),
        "photo": (data.get("photo") or "").strip(),
        "active": 1 if data.get("active", True) else 0,
        "category": (data.get("category") or "").strip(),
    }


@app.route("/api/admin/products", methods=["POST"])
@login_required
def admin_create_product():
    data = request.get_json(force=True, silent=True) or {}
    p = parse_product_input(data)
    if not p["name"]:
        return jsonify({"error": "El producto necesita un nombre."}), 400
    if p["price_cents"] <= 0:
        return jsonify({"error": "El precio debe ser mayor a cero."}), 400
    db = get_db()
    cur = db.execute(
        """INSERT INTO products(name, description, price_cents, cost, sizes, sku, stock, photo, active, category, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            p["name"], p["description"], p["price_cents"], p["cost_cents"] / 100.0,
            p["sizes"], p["sku"],
            p["stock"], p["photo"], p["active"], p["category"], int(time.time()),
        ),
    )
    pid = cur.lastrowid
    upload_ids = data.get("photo_upload_ids") or []
    if isinstance(upload_ids, str):
        upload_ids = [upload_ids]
    upload_ids = [u.strip() for u in upload_ids if u and u.strip()]
    if not upload_ids:
        single = (data.get("photo_upload_id") or "").strip()
        if single:
            upload_ids = [single]
    if upload_ids:
        _attach_pending_photo(db, pid, upload_ids[0])
        for uid in upload_ids[1:]:
            _add_gallery_photo(db, pid, uid)
    db.commit()
    row = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_to_dict(row, _gallery_items(db, pid), include_cost=True)), 201


@app.route("/api/admin/products/<int:pid>", methods=["PUT"])
@login_required
def admin_update_product(pid):
    data = request.get_json(force=True, silent=True) or {}
    p = parse_product_input(data)
    if not p["name"]:
        return jsonify({"error": "El producto necesita un nombre."}), 400
    db = get_db()
    cur = db.execute(
        """UPDATE products SET name=?, description=?, price_cents=?, cost=?, sizes=?, sku=?,
           stock=?, photo=?, active=?, category=? WHERE id=?""",
        (
            p["name"], p["description"], p["price_cents"], p["cost_cents"] / 100.0,
            p["sizes"], p["sku"],
            p["stock"], p["photo"], p["active"], p["category"], pid,
        ),
    )
    upload_id = (data.get("photo_upload_id") or "").strip()
    if upload_id:
        _attach_pending_photo(db, pid, upload_id)
    elif not p["photo"]:
        # Se quitó la foto: borrar el blob guardado
        db.execute("UPDATE products SET photo_blob=NULL, photo_mime='' WHERE id=?", (pid,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Producto no encontrado."}), 404
    row = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_to_dict(row, _gallery_items(db, pid), include_cost=True))


@app.route("/api/admin/products/<int:pid>", methods=["DELETE"])
@login_required
def admin_delete_product(pid):
    db = get_db()
    row = db.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone()
    if not row:
        return jsonify({"error": "Producto no encontrado."}), 404
    # La foto (blob) se borra junto con la fila del producto.
    db.execute("DELETE FROM products WHERE id=?", (pid,))
    db.execute("DELETE FROM product_photos WHERE product_id=?", (pid,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/products/<int:pid>/qr")
@login_required
def admin_product_qr(pid):
    """QR del producto para el cliente: al escanearlo abre la página del
    producto en la tienda (fotos, precio, detalles)."""
    db = get_db()
    row = db.execute("SELECT id, name FROM products WHERE id=?", (pid,)).fetchone()
    if not row:
        return jsonify({"error": "Producto no encontrado."}), 404
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    url = scheme + "://" + request.host + "/#p-" + str(pid)
    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=10, border=2)
    return Response(buf.getvalue(), mimetype="image/png")


@app.route("/api/admin/stats/profit")
@login_required
def admin_profit_stats():
    """Estadísticas de ganancias del inventario para las gráficas del admin.

    Devuelve los totales (inversión, valor a precio de venta, ganancia
    potencial y margen promedio) y la ganancia por producto, calculados sobre
    los productos que tienen stock. Los productos sin costo registrado se
    cuentan aparte para avisar en el panel.
    """
    db = get_db()
    rows = db.execute("SELECT * FROM products").fetchall()
    items = []
    investment = 0
    sale_value = 0
    profit = 0
    sin_costo = 0
    for r in rows:
        stock = r["stock"] or 0
        if stock <= 0:
            continue
        price = r["price_cents"] or 0
        cost = _pcol_cost(r)
        if cost <= 0:
            sin_costo += 1
        unit_profit = price - cost
        total_profit = unit_profit * stock
        investment += cost * stock
        sale_value += price * stock
        profit += total_profit
        items.append(
            {
                "id": r["id"],
                "name": r["name"],
                "price_cents": price,
                "cost_cents": cost,
                "stock": stock,
                "unit_profit_cents": unit_profit,
                "total_profit_cents": total_profit,
            }
        )
    items.sort(key=lambda x: x["total_profit_cents"], reverse=True)
    avg_margin = round(profit / sale_value * 100, 1) if sale_value > 0 else 0.0
    return jsonify(
        {
            "investment_cents": investment,
            "sale_value_cents": sale_value,
            "potential_profit_cents": profit,
            "avg_margin_pct": avg_margin,
            "products_with_stock": len(items),
            "products_without_cost": sin_costo,
            "by_product": items,
        }
    )


def _attach_pending_photo(db, pid, upload_id):
    """Mueve la foto subida (pending_uploads) al producto. Devuelve True si la adjuntó."""
    if not upload_id:
        return False
    row = db.execute(
        "SELECT data, mime FROM pending_uploads WHERE id=?", (upload_id,)
    ).fetchone()
    if not row or not row["data"]:
        return False
    db.execute(
        "UPDATE products SET photo_blob=?, photo_mime=?, photo=? WHERE id=?",
        (bytes(row["data"]), row["mime"] or "image/jpeg", f"/api/photo/{pid}", pid),
    )
    db.execute("DELETE FROM pending_uploads WHERE id=?", (upload_id,))
    return True


def _add_gallery_photo(db, pid, upload_id):
    """Mueve una subida pendiente a la galería del producto. Devuelve el id o None."""
    if not upload_id:
        return None
    if not db.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone():
        return None
    row = db.execute(
        "SELECT data, mime FROM pending_uploads WHERE id=?", (upload_id,)
    ).fetchone()
    if not row or not row["data"]:
        return None
    pos = db.execute(
        "SELECT COALESCE(MAX(position), -1)+1 FROM product_photos WHERE product_id=?",
        (pid,),
    ).fetchone()[0]
    cur = db.execute(
        "INSERT INTO product_photos(product_id, data, mime, position, created_at)"
        " VALUES(?,?,?,?,?)",
        (pid, bytes(row["data"]), row["mime"] or "image/jpeg", pos, int(time.time())),
    )
    db.execute("DELETE FROM pending_uploads WHERE id=?", (upload_id,))
    return cur.lastrowid


def _promote_to_cover(db, pid, photo_id):
    """Mueve una foto de la galería a la portada del producto."""
    row = db.execute(
        "SELECT data, mime FROM product_photos WHERE id=? AND product_id=?",
        (photo_id, pid),
    ).fetchone()
    if not row or not row["data"]:
        return False
    db.execute(
        "UPDATE products SET photo_blob=?, photo_mime=?, photo=? WHERE id=?",
        (bytes(row["data"]), row["mime"] or "image/jpeg", f"/api/photo/{pid}", pid),
    )
    db.execute("DELETE FROM product_photos WHERE id=?", (photo_id,))
    return True


@app.route("/api/admin/products/<int:pid>/photos", methods=["POST"])
@login_required
def admin_add_photo(pid):
    """Agrega una foto (ya subida vía /api/upload) a la galería del producto."""
    data = request.get_json(force=True, silent=True) or {}
    uid = (data.get("upload_id") or "").strip()
    if not uid:
        return jsonify({"error": "Falta upload_id."}), 400
    db = get_db()
    new_id = _add_gallery_photo(db, pid, uid)
    if not new_id:
        return jsonify({"error": "No se pudo agregar la foto."}), 400
    # Si el producto no tiene portada, la primera foto de la galería la hereda.
    cov = db.execute("SELECT photo_blob FROM products WHERE id=?", (pid,)).fetchone()
    if cov is not None and not cov["photo_blob"]:
        _promote_to_cover(db, pid, new_id)
    db.commit()
    return jsonify(_photo_state(db, pid)), 201


@app.route("/api/admin/products/<int:pid>/photos/<int:photo_id>", methods=["DELETE"])
@login_required
def admin_delete_photo(pid, photo_id):
    db = get_db()
    cur = db.execute(
        "DELETE FROM product_photos WHERE id=? AND product_id=?", (photo_id, pid)
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Foto no encontrada."}), 404
    return jsonify(_photo_state(db, pid))


@app.route("/api/admin/products/<int:pid>/photos/<int:photo_id>/cover", methods=["POST"])
@login_required
def admin_photo_cover(pid, photo_id):
    """Hace que una foto de la galería sea la portada del producto."""
    db = get_db()
    if not _promote_to_cover(db, pid, photo_id):
        return jsonify({"error": "Foto no encontrada."}), 404
    db.commit()
    return jsonify(_photo_state(db, pid))


@app.route("/api/product-photo/<int:photo_id>")
def serve_gallery_photo(photo_id):
    """Sirve una foto de la galería de un producto."""
    db = get_db()
    row = db.execute(
        "SELECT data, mime FROM product_photos WHERE id=?", (photo_id,)
    ).fetchone()
    if not row or not row["data"]:
        return jsonify({"error": "No encontrado."}), 404
    return app.response_class(
        bytes(row["data"]), mimetype=row["mime"] or "image/jpeg"
    )


@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No se envió ningún archivo."}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Archivo sin nombre."}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXT:
        return jsonify({"error": "Solo se permiten imágenes (PNG, JPG, WEBP, GIF)."}), 400
    data = f.stream.read()
    if not _is_image(data[:12]):
        return jsonify({"error": "El archivo no es una imagen válida."}), 400
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "gif": "image/gif",
    }[ext]
    uid = uuid.uuid4().hex
    db = get_db()
    # Limpieza: borrar subidas pendientes de hace más de 1 día
    db.execute(
        "DELETE FROM pending_uploads WHERE created_at < ?",
        (int(time.time()) - 86400,),
    )
    db.execute(
        "INSERT INTO pending_uploads(id, data, mime, created_at) VALUES(?,?,?,?)",
        (uid, data, mime, int(time.time())),
    )
    db.commit()
    return jsonify({"url": f"/api/photo/pending/{uid}", "upload_id": uid}), 201


@app.route("/api/photo/pending/<uid>")
def photo_pending(uid):
    """Sirve una foto recién subida (vista previa antes de guardar el producto)."""
    db = get_db()
    row = db.execute(
        "SELECT data, mime FROM pending_uploads WHERE id=?", (uid,)
    ).fetchone()
    if not row or not row["data"]:
        return jsonify({"error": "No encontrado."}), 404
    return app.response_class(bytes(row["data"]), mimetype=row["mime"] or "image/jpeg")


@app.route("/api/photo/<int:pid>")
def photo_product(pid):
    """Sirve la foto guardada de un producto."""
    db = get_db()
    row = db.execute(
        "SELECT photo_blob, photo_mime FROM products WHERE id=?", (pid,)
    ).fetchone()
    if not row or not row["photo_blob"]:
        return jsonify({"error": "No encontrado."}), 404
    return app.response_class(
        bytes(row["photo_blob"]), mimetype=row["photo_mime"] or "image/jpeg"
    )


def _is_image(head: bytes) -> bool:
    for magic in MAGIC_BYTES:
        if head.startswith(magic):
            if magic == b"RIFF":
                return head[8:12] == b"WEBP"
            return True
    return False


# ---------------- API admin: pedidos ----------------
@app.route("/api/admin/orders")
@login_required
def admin_list_orders():
    db = get_db()
    rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 200").fetchall()
    # Ítems parseados + mapa de productos con foto de portada (miniaturas).
    parsed = []
    pids = set()
    for r in rows:
        items = _parse_order_items(r["items"])
        parsed.append(items)
        for it in items:
            pid = it.get("id")
            if isinstance(pid, int):
                pids.add(pid)
    with_photo = set()
    if pids:
        q = ",".join("?" for _ in pids)
        for pr in db.execute(
            f"SELECT id FROM products WHERE id IN ({q}) "
            "AND photo_blob IS NOT NULL AND photo_blob != ''",
            list(pids),
        ):
            with_photo.add(pr["id"])
    out = []
    for r, items in zip(rows, parsed):
        for it in items:
            pid = it.get("id")
            it["photo_url"] = f"/api/photo/{pid}" if pid in with_photo else None
        out.append(
            {
                "id": r["id"],
                "items": items,
                "total_cents": r["total_cents"],
                "status": r["status"],
                "created_at": r["created_at"],
                "customer_name": r["customer_name"] or "",
                "customer_address": r["customer_address"] or "",
                "customer_city": r["customer_city"] or "",
                "customer_department": r["customer_department"] or "",
                "delivery_method": r["delivery_method"] or "",
                "payment_method": r["payment_method"] or "",
                "shipping_cents": r["shipping_cents"] if r["shipping_cents"] else 0,
                "auto_paid": bool(r["auto_paid"]),
                "customer_phone": _order_col(r, "customer_phone", ""),
                "authorized_receiver": _order_col(r, "authorized_receiver", ""),
                "whatsapp_optin": bool(_order_col(r, "whatsapp_optin", 0)),
                "fulfillment_status": _order_col(r, "fulfillment_status", "pending") or "pending",
                "tracking_number": _order_col(r, "tracking_number", ""),
            }
        )
    return jsonify(out)


@app.route("/api/admin/orders/<int:order_id>/paid", methods=["POST"])
@login_required
def admin_mark_order_paid(order_id):
    """El dueño confirma que recibió el pago (efectivo o depósito):
    marca el pedido como pagado y descuenta el stock."""
    _finalize_order(order_id)
    return jsonify({"ok": True})


def _restore_stock(db, order_row):
    """Devuelve al inventario las unidades de un pedido (al revertir un pago
    o al eliminar un pedido que ya estaba pagado)."""
    try:
        items = _parse_order_items(order_row["items"])
    except Exception:
        return
    for it in items:
        qty = it.get("qty")
        pid = it.get("id")
        if isinstance(qty, int) and isinstance(pid, int):
            db.execute(
                "UPDATE products SET stock = stock + ? WHERE id=?",
                (qty, pid),
            )


@app.route("/api/admin/orders/<int:order_id>/unpaid", methods=["POST"])
@login_required
def admin_mark_order_unpaid(order_id):
    """Revierte un pedido pagado a pendiente y devuelve las unidades al stock.
    Los pedidos con pago automático confirmado están protegidos."""
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return jsonify({"error": "Pedido no encontrado."}), 404
    if row["status"] == "paid":
        if row["auto_paid"]:
            return jsonify({"error": "No se puede revertir: el pago fue confirmado automáticamente."}), 403
        _restore_stock(db, row)
        db.execute(
            "UPDATE orders SET status='pending', fulfillment_status='pending' WHERE id=?",
            (order_id,),
        )
        db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/orders/<int:order_id>/status", methods=["POST"])
@login_required
def admin_order_fulfillment_status(order_id):
    """Cambia el estado de cumplimiento del pedido (empaquetamiento, listo,
    enviado, entregado, cancelado) y opcionalmente guarda el número de guía.
    Al pasar a 'ready' se envía el aviso de WhatsApp si el cliente aceptó."""
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("status") or "").strip()
    if status not in FULFILLMENT_STATES:
        return jsonify({"error": "Estado no válido."}), 400
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return jsonify({"error": "Pedido no encontrado."}), 404
    tracking = data.get("tracking_number")
    if tracking is None:
        tracking = _order_col(row, "tracking_number", "")
    else:
        tracking = str(tracking).strip()
    db.execute(
        "UPDATE orders SET fulfillment_status=?, tracking_number=? WHERE id=?",
        (status, tracking, order_id),
    )
    db.commit()
    if status == "ready":
        _maybe_send_whatsapp(db, row, "listo", tracking)
    return jsonify({"ok": True, "fulfillment_status": status,
                    "tracking_number": tracking})


@app.route("/api/admin/orders/<int:order_id>", methods=["DELETE"])
@login_required
def admin_delete_order(order_id):
    """Elimina un pedido. Si ya estaba pagado, devuelve el stock primero.
    Los pedidos con pago automático confirmado no se pueden eliminar."""
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return jsonify({"error": "Pedido no encontrado."}), 404
    if row["status"] == "paid" and row["auto_paid"]:
        return jsonify({"error": "No se puede eliminar: el pago fue confirmado automáticamente."}), 403
    if row["status"] == "paid":
        _restore_stock(db, row)
    db.execute("DELETE FROM orders WHERE id=?", (order_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------------- API admin: ajustes ----------------
SETTING_KEYS = ["store_name", "currency", "stripe_secret_key",
                "stripe_publishable_key", "stripe_webhook_secret",
                "tagline", "info_horarios", "info_ubicacion",
                "info_contacto", "info_pagos", "info_envios"]


# Textos informativos que se muestran en la tienda pública.
INFO_DEFAULTS = {
    "tagline": "Tu nuevo estilo comienza aquí",
    "info_horarios": "Lunes a sábado, 9:00 AM – 6:00 PM.",
    "info_ubicacion": "Honduras. Hacemos envíos a todo el país.",
    "info_contacto": "Escríbenos para consultas y pedidos. Con gusto te atenderemos.",
    "info_pagos": "💵 Efectivo (pago contra entrega) y 🏦 depósito o transferencia en Banco Atlántida (depósito previo). Escríbenos por WhatsApp al +504 9527-3914 y te pasamos los datos de la cuenta.",
    "info_envios": "📍 Recoger en persona en Puerto Castilla: gratis. Te avisamos por WhatsApp cuando tu pedido esté listo. 💳 Por el momento solo aceptamos depósito en Banco Atlántida.",
}


@app.route("/api/info")
def api_info():
    """Información pública de la tienda: nombre, eslogan y secciones (pagos, envíos, etc.)."""
    out = {"store_name": get_setting("store_name", "Tu Nuevo Estilo"), "currency": get_setting("currency", "hnl")}
    for k, default in INFO_DEFAULTS.items():
        v = (get_setting(k) or "").strip()
        out[k] = v or default
    return jsonify(out)


@app.route("/api/settings/public")
def api_settings_public():
    """Ajustes públicos no sensibles: solo la URL del canal de WhatsApp
    (vacía hasta que el dueño la configure en Ajustes)."""
    return jsonify({"whatsapp_channel_url": get_setting("whatsapp_channel_url", "")})


@app.route("/api/admin/settings", methods=["GET"])
@login_required
def admin_get_settings():
    out = {
        "store_name": get_setting("store_name", "Tu Nuevo Estilo"),
        "currency": get_setting("currency", "hnl"),
        "tagline": get_setting("tagline", INFO_DEFAULTS["tagline"]),
        "whatsapp_channel_url": get_setting("whatsapp_channel_url", ""),
        "info_horarios": get_setting("info_horarios", ""),
        "info_ubicacion": get_setting("info_ubicacion", ""),
        "info_contacto": get_setting("info_contacto", ""),
        "info_pagos": get_setting("info_pagos", ""),
        "info_envios": get_setting("info_envios", ""),
    }
    for k in ["stripe_secret_key", "stripe_publishable_key", "stripe_webhook_secret"]:
        v = get_setting(k)
        out[k] = {"configured": bool(v), "last4": v[-4:] if v else ""}
    return jsonify(out)


@app.route("/api/admin/settings", methods=["PUT"])
@login_required
def admin_put_settings():
    data = request.get_json(force=True, silent=True) or {}
    if "store_name" in data:
        set_setting("store_name", (data["store_name"] or "").strip() or "Tu Nuevo Estilo")
    if "currency" in data:
        cur = (data["currency"] or "hnl").strip().lower()
        set_setting("currency", cur if len(cur) == 3 else "hnl")
    for k in ["tagline", "info_horarios", "info_ubicacion",
              "info_contacto", "info_pagos", "info_envios"]:
        if k in data:
            set_setting(k, (data[k] or "").strip())
    if "whatsapp_channel_url" in data:
        url = (data["whatsapp_channel_url"] or "").strip()
        if len(url) > 500:
            return jsonify({"error": "La URL es demasiado larga."}), 400
        if url and not (url.startswith("http://") or url.startswith("https://")):
            return jsonify({"error": "La URL del canal de WhatsApp debe empezar con http:// o https://"}), 400
        set_setting("whatsapp_channel_url", url)
    for k in ["stripe_secret_key", "stripe_publishable_key", "stripe_webhook_secret"]:
        if k in data and data[k]:
            set_setting(k, data[k].strip())
    return jsonify({"ok": True})


# ---------------- Notificaciones push del admin (Web Push / VAPID) ----------------
# Cuando un cliente crea un pedido, el admin recibe una notificación push en
# su teléfono, como las de otras apps. Requiere las variables de entorno
# VAPID_PUBLIC_KEY y VAPID_PRIVATE_KEY (se configuran en Render; sin ellas
# el push queda desactivado sin romper nada).
_PUSH_SYNC = False  # en True ejecuta el envío en el mismo hilo (para tests)


def _vapid_configured():
    return (
        _HAS_PYWEBPUSH
        and bool(os.environ.get("VAPID_PUBLIC_KEY"))
        and bool(os.environ.get("VAPID_PRIVATE_KEY"))
    )


def _push_send_all(title, body, tag="", url="/admin"):
    """Envía una notificación push a todas las suscripciones del admin.

    Nunca lanza excepciones: si VAPID no está configurado o no hay
    suscripciones, no hace nada. Las suscripciones muertas (410/404) se
    eliminan. Abre su propia conexión porque puede correr en un hilo aparte.
    """
    if not _vapid_configured():
        return
    try:
        db = _connect_db()
        subs = db.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions"
        ).fetchall()
        db.close()
    except Exception:
        return
    if not subs:
        return
    payload = json.dumps({"title": title, "body": body, "tag": tag, "url": url})
    priv = os.environ.get("VAPID_PRIVATE_KEY") or ""
    dead = []
    for s in subs:
        try:
            _pywebpush_send(
                {
                    "endpoint": s["endpoint"],
                    "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
                },
                payload,
                vapid_private_key=priv,
                vapid_claims={"sub": "mailto:tienda@tunuevoestilo.hn"},
                timeout=10,
            )
        except Exception as e:
            resp = getattr(e, "response", None)
            if getattr(resp, "status_code", None) in (404, 410):
                dead.append(s["endpoint"])
    if dead:
        try:
            db = _connect_db()
            for ep in dead:
                db.execute(
                    "DELETE FROM push_subscriptions WHERE endpoint=?", (ep,)
                )
            db.commit()
            db.close()
        except Exception:
            pass


def _push_new_order_now(order_id, customer_name, total_cents, payment):
    """Construye el aviso de nuevo pedido y lo envía (parte síncrona)."""
    pay_label = {"efectivo": "Efectivo", "deposito": "Depósito"}.get(
        payment or "", ""
    )
    body = "%s · L %s%s" % (
        customer_name or "Cliente",
        "{:,.2f}".format((total_cents or 0) / 100),
        (" · " + pay_label) if pay_label else "",
    )
    _push_send_all(
        "🧾 Nuevo pedido #%d" % order_id, body, tag="pedido-%d" % order_id
    )


def _notify_new_order(order_id, customer_name, total_cents, payment):
    """Avisa al admin cuando se crea un pedido. Corre en un hilo aparte para
    no retrasar la respuesta del checkout; los fallos nunca afectan el pedido."""
    if _PUSH_SYNC:
        try:
            _push_new_order_now(order_id, customer_name, total_cents, payment)
        except Exception:
            pass
        return

    import threading

    def _run():
        try:
            _push_new_order_now(order_id, customer_name, total_cents, payment)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


@app.route("/api/admin/vapid-public-key")
@login_required
def admin_vapid_public_key():
    """Clave pública VAPID para suscribir el navegador del admin."""
    return jsonify({"public_key": os.environ.get("VAPID_PUBLIC_KEY") or ""})


@app.route("/api/admin/push/subscribe", methods=["POST"])
@login_required
def admin_push_subscribe():
    data = request.get_json(force=True, silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "Suscripción incompleta."}), 400
    if len(endpoint) > 2000:
        return jsonify({"error": "Endpoint demasiado largo."}), 400
    db = get_db()
    db.execute(
        "INSERT INTO push_subscriptions(endpoint, p256dh, auth, created_at)"
        " VALUES(?,?,?,?)"
        " ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh,"
        " auth=excluded.auth",
        (endpoint, p256dh, auth, int(time.time())),
    )
    db.commit()
    return jsonify({"ok": True, "vapid_configured": _vapid_configured()})


@app.route("/api/admin/push/unsubscribe", methods=["POST"])
@login_required
def admin_push_unsubscribe():
    data = request.get_json(force=True, silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    db = get_db()
    if endpoint:
        db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
    else:
        db.execute("DELETE FROM push_subscriptions")
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/push/status")
@login_required
def admin_push_status():
    db = get_db()
    row = db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()
    return jsonify(
        {
            "vapid_configured": _vapid_configured(),
            "subscriptions": row[0] if row else 0,
        }
    )


# ---------------- Checkout con Stripe ----------------
# Estados de cumplimiento del pedido (fulfillment_status).
# pending:   pendiente de pago / recién creado
# packing:   pago confirmado, en proceso de empaquetamiento
# ready:     paquete envuelto y listo para que lo recoja la compañía de envíos
# shipped:   enviado (en camino)
# delivered: entregado
# cancelled: cancelado
FULFILLMENT_STATES = {
    "pending": "Pendiente de pago",
    "packing": "En empaquetamiento",
    "ready": "Listo para envío",
    "shipped": "Enviado",
    "delivered": "Entregado",
    "cancelled": "Cancelado",
}


def norm_phone(raw):
    """Solo dígitos; quita el prefijo 504/+504 si viene con él."""
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    if len(digits) == 11 and digits.startswith("504"):
        digits = digits[3:]
    return digits


def valid_phone(raw):
    """Celular hondureño: 8 dígitos (permite +504/504, guiones, espacios)."""
    return len(norm_phone(raw)) == 8


def _order_col(row, name, default=""):
    """Lee una columna de orders tolerando BDs viejas sin la columna."""
    try:
        v = row[name]
    except (KeyError, ValueError, IndexError):
        return default
    return default if v is None else v


def _parse_order_items(items_json):
    """Devuelve los ítems de un pedido como lista de dicts.

    Tolera datos viejos o inesperados en la BD: cualquier cosa que no sea
    una lista de objetos se convierte en lista vacía (y se filtran los
    elementos que no sean objetos). Nunca lanza excepciones, para que un
    pedido con datos raros no tumbe la lista completa de pedidos.
    """
    try:
        items = json.loads(items_json)
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


def _maybe_send_whatsapp(db, order_row, kind, tracking_number=None):
    """Envía el aviso de WhatsApp si el cliente aceptó (opt-in) y el módulo
    está configurado. Nunca lanza excepciones ni rompe el flujo del pedido."""
    try:
        if not whatsapp_cloud:
            return
        if int(_order_col(order_row, "whatsapp_optin", 0) or 0) != 1:
            return
        phone = _order_col(order_row, "customer_phone", "")
        name = _order_col(order_row, "customer_name", "")
        whatsapp_cloud.send_order_update(
            phone, name, order_row["id"], kind, tracking_number
        )
    except Exception:
        pass


def _validate_cart(items):
    """Valida el carrito contra la BD. Devuelve (line_items, total_cents, snapshot) o lanza ValueError."""
    if not isinstance(items, list) or not items:
        raise ValueError("El carrito está vacío.")
    db = get_db()
    currency = get_setting("currency", "hnl")
    line_items = []
    snapshot = []
    total = 0
    for it in items:
        try:
            pid = int(it.get("id"))
            qty = int(it.get("qty", 1))
        except (TypeError, ValueError):
            raise ValueError("Artículo inválido en el carrito.")
        if qty < 1 or qty > 99:
            raise ValueError("Cantidad inválida.")
        row = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not row or not row["active"]:
            raise ValueError("Un producto ya no está disponible.")
        if row["stock"] < qty:
            raise ValueError(f'Stock insuficiente para "{row["name"]}".')
        size = (it.get("size") or "").strip()
        sizes = json.loads(row["sizes"] or "[]")
        if sizes and size not in sizes:
            raise ValueError(f'Talla inválida para "{row["name"]}".')
        line_items.append(
            {
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": row["name"]},
                    "unit_amount": row["price_cents"],
                },
                "quantity": qty,
            }
        )
        snapshot.append(
            {
                "id": row["id"],
                "name": row["name"],
                "price_cents": row["price_cents"],
                "size": size,
                "qty": qty,
            }
        )
        total += row["price_cents"] * qty
    return line_items, total, snapshot


@app.route("/api/checkout", methods=["POST"])
def api_checkout():
    """Crea el pedido con la forma de pago elegida (efectivo / depósito).

    Ya no redirige a Stripe: el pedido queda 'pending' y el dueño lo marca
    como pagado desde el admin cuando recibe el efectivo o el depósito.
    """
    data = request.get_json(force=True, silent=True) or {}
    customer = data.get("customer") or {}
    name = (customer.get("name") or "").strip()
    address = (customer.get("address") or "").strip()
    city = (customer.get("city") or "").strip()
    department = (customer.get("department") or "").strip()
    delivery = (customer.get("delivery") or "").strip()
    payment = (customer.get("payment") or "").strip()
    phone = (customer.get("phone") or "").strip()
    # Persona autorizada a recibir el pedido (opcional, solo domicilio).
    authorized_receiver = (customer.get("authorized_receiver") or "").strip()[:120]
    whatsapp_optin = 1 if customer.get("whatsapp_optin") else 0
    if not name or not address or not city or not department:
        return (
            jsonify(
                {
                    "error": "Completa tu nombre, dirección, ciudad y departamento. "
                    "Si no ves esos campos, actualiza la app: ciérrala por completo "
                    "y vuelve a abrirla."
                }
            ),
            400,
        )
    if not valid_phone(phone):
        return (
            jsonify(
                {"error": "Escribe un número de celular válido de 8 dígitos."}
            ),
            400,
        )
    if delivery != "oficina":
        return (
            jsonify(
                {
                    "error": "Por el momento solo ofrecemos recoger en persona en Puerto Castilla."
                }
            ),
            400,
        )
    if payment not in ("efectivo", "deposito"):
        return (
            jsonify({"error": "Elige la forma de pago: efectivo o depósito."}),
            400,
        )
    try:
        _line_items, total, snapshot = _validate_cart(data.get("items"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Solo recoger en persona (Puerto Castilla): sin costo de envío.
    total_qty = sum(s["qty"] for s in snapshot)
    shipping = 0
    total += shipping

    db = get_db()
    cur = db.execute(
        "INSERT INTO orders(items, total_cents, shipping_cents, status, created_at, "
        "customer_name, customer_address, customer_city, customer_department, "
        "delivery_method, payment_method, customer_phone, authorized_receiver, "
        "whatsapp_optin, fulfillment_status) "
        "VALUES(?,?,?, 'pending', ?,?,?,?,?,?,?,?,?,?,'pending')",
        (
            json.dumps(snapshot),
            total,
            shipping,
            int(time.time()),
            name,
            address,
            city,
            department,
            delivery,
            payment,
            norm_phone(phone),
            authorized_receiver,
            whatsapp_optin,
        ),
    )
    order_id = cur.lastrowid
    db.commit()
    # Avisar al admin con notificación push (en segundo plano; si falla,
    # el pedido ya quedó creado y no se ve afectado).
    _notify_new_order(order_id, name, total, payment)
    return jsonify(
        {
            "order_id": order_id,
            "total_cents": total,
            "shipping_cents": shipping,
            "delivery": delivery,
            "payment": payment,
            "authorized_receiver": authorized_receiver,
        }
    )


def _finalize_order(order_id, auto=False):
    """Marca el pedido como pagado y descuenta el stock. Idempotente.
    auto=True cuando lo confirma un pago automático (tarjeta/cuenta vía
    pasarela): esos pedidos quedan protegidos, no se pueden borrar ni
    revertir desde el admin."""
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row or row["status"] == "paid":
        return
    items = _parse_order_items(row["items"])
    for it in items:
        qty = it.get("qty")
        pid = it.get("id")
        if not (isinstance(qty, int) and isinstance(pid, int)):
            continue
        db.execute(
            "UPDATE products SET stock = MAX(0, stock - ?) WHERE id=?",
            (qty, pid),
        )
    if auto:
        db.execute("UPDATE orders SET status='paid', auto_paid=1 WHERE id=?", (order_id,))
    else:
        db.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,))
    # Al confirmarse el pago, el pedido entra en proceso de empaquetamiento.
    if (_order_col(row, "fulfillment_status", "pending") or "pending") == "pending":
        db.execute(
            "UPDATE orders SET fulfillment_status='packing' WHERE id=?", (order_id,)
        )
    db.commit()
    # Aviso automático por WhatsApp (solo si el cliente aceptó y el módulo
    # está configurado). Se hace después del commit para no bloquear el pago.
    _maybe_send_whatsapp(db, row, "confirmado")


@app.route("/api/stripe-webhook", methods=["POST"])
def api_stripe_webhook():
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    webhook_secret = get_setting("stripe_webhook_secret")
    secret = get_setting("stripe_secret_key")
    if not secret:
        return jsonify({"error": "Stripe no configurado."}), 400
    stripe.api_key = secret
    try:
        if webhook_secret:
            event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
        else:
            app.logger.warning("Webhook sin firma verificada (no hay webhook secret).")
            event = json.loads(payload)
    except Exception as e:
        return jsonify({"error": f"Firma inválida: {e}"}), 400

    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        order_id = (sess.get("metadata") or {}).get("order_id")
        if order_id:
            with app.app_context():
                _finalize_order(int(order_id))
    return jsonify({"received": True})


@app.route("/api/confirm-payment", methods=["POST"])
def api_confirm_payment():
    """Plan B si el webhook no está configurado: verifica la sesión en Stripe."""
    secret = get_setting("stripe_secret_key")
    if not secret:
        return jsonify({"error": "Stripe no configurado."}), 400
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "")
    if not session_id:
        return jsonify({"error": "Falta session_id."}), 400
    stripe.api_key = secret
    try:
        sess = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        return jsonify({"error": f"No se pudo verificar el pago: {e}"}), 502
    if sess.payment_status == "paid":
        order_id = (sess.metadata or {}).get("order_id")
        if order_id:
            _finalize_order(int(order_id))
        return jsonify({"paid": True})
    return jsonify({"paid": False})


# ---------------- Arranque ----------------
def main():
    init_db()
    # Pre-cargar contraseña de admin desde variable de entorno (solo si no existe)
    env_pw = os.environ.get("ADMIN_PASSWORD")
    with app.app_context():
        if env_pw and not admin_password_set():
            set_setting("admin_password_hash", generate_password_hash(env_pw))
            print("Contraseña de admin creada desde ADMIN_PASSWORD.")
        if not admin_password_set():
            print("AVISO: aún no hay contraseña de admin. Créala en /admin/setup")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Tienda lista en http://localhost:{port}  |  Admin en http://localhost:{port}/admin")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
