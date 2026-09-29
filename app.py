#!/usr/bin/env python3
"""
Tienda de ropa - sistema completo.
- GET /        -> tienda pública para clientes (PWA instalable)
- GET /admin   -> app privada de administración (PWA instalable, con contraseña)
Toda la interfaz en español, mobile-first.
"""
import os
import json
import sqlite3
import secrets
import uuid
import time
from functools import wraps

from flask import (
    Flask, request, jsonify, redirect, session,
    send_from_directory, render_template, g,
)
from werkzeug.security import generate_password_hash, check_password_hash

import stripe

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
        for r in self._cur:
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
            created_at INTEGER NOT NULL
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
        """
    )
    # Migración de moneda: la tienda ahora usa lempiras (HNL).
    # Si la BD ya tenía "usd" guardado, se cambia a "hnl" automáticamente.
    row = db.execute("SELECT value FROM settings WHERE key='currency'").fetchone()
    if row and (row[0] or "").strip().lower() == "usd":
        db.execute("UPDATE settings SET value='hnl' WHERE key='currency'")
    # Migración: columna category en products (para filtrar por categorías).
    cols = [c[1] for c in db.execute("PRAGMA table_info(products)").fetchall()]
    if "category" not in cols:
        db.execute("ALTER TABLE products ADD COLUMN category TEXT DEFAULT ''")
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


def product_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "price_cents": row["price_cents"],
        "sizes": json.loads(row["sizes"] or "[]"),
        "sku": row["sku"] or "",
        "stock": row["stock"],
        "photo": row["photo"] or "",
        "active": bool(row["active"]),
        "category": row["category"] or "",
        "created_at": row["created_at"],
    }


def admin_password_set():
    return bool(get_setting("admin_password_hash"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "No autorizado. Inicia sesión."}), 401
            return redirect("/admin/login")
        return view(*args, **kwargs)

    return wrapped


# ---------------- Páginas ----------------
@app.route("/")
def store():
    return render_template(
        "store.html",
        store_name=get_setting("store_name", "Mi Tienda de Ropa"),
    )


@app.route("/exito")
def success():
    return render_template("success.html")


@app.route("/cancelado")
def cancelled():
    return render_template("cancel.html")


@app.route("/admin")
def admin_index():
    if not admin_password_set():
        return redirect("/admin/setup")
    if not session.get("admin"):
        return redirect("/admin/login")
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
    return redirect("/admin/login")


# PWA: manifest dinámico (usa el nombre de la tienda)
@app.route("/manifest.json")
def manifest():
    store_name = get_setting("store_name", "Mi Tienda de Ropa")
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
            "name": "Tienda Admin",
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
    return jsonify({"ok": True})


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password") or ""
    if admin_password_set() and check_password_hash(
        get_setting("admin_password_hash"), password
    ):
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "Contraseña incorrecta."}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.route("/api/admin/me")
@login_required
def api_me():
    return jsonify({"ok": True, "store_name": get_setting("store_name", "Mi Tienda de Ropa")})


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
    return jsonify([product_to_dict(r) for r in rows])


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
    return jsonify([product_to_dict(r) for r in rows])


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
    return {
        "name": (data.get("name") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "price_cents": max(0, price_cents),
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
        """INSERT INTO products(name, description, price_cents, sizes, sku, stock, photo, active, category, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            p["name"], p["description"], p["price_cents"], p["sizes"], p["sku"],
            p["stock"], p["photo"], p["active"], p["category"], int(time.time()),
        ),
    )
    pid = cur.lastrowid
    _attach_pending_photo(db, pid, (data.get("photo_upload_id") or "").strip())
    db.commit()
    row = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_to_dict(row)), 201


@app.route("/api/admin/products/<int:pid>", methods=["PUT"])
@login_required
def admin_update_product(pid):
    data = request.get_json(force=True, silent=True) or {}
    p = parse_product_input(data)
    if not p["name"]:
        return jsonify({"error": "El producto necesita un nombre."}), 400
    db = get_db()
    cur = db.execute(
        """UPDATE products SET name=?, description=?, price_cents=?, sizes=?, sku=?,
           stock=?, photo=?, active=?, category=? WHERE id=?""",
        (
            p["name"], p["description"], p["price_cents"], p["sizes"], p["sku"],
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
    return jsonify(product_to_dict(row))


@app.route("/api/admin/products/<int:pid>", methods=["DELETE"])
@login_required
def admin_delete_product(pid):
    db = get_db()
    row = db.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone()
    if not row:
        return jsonify({"error": "Producto no encontrado."}), 404
    # La foto (blob) se borra junto con la fila del producto.
    db.execute("DELETE FROM products WHERE id=?", (pid,))
    db.commit()
    return jsonify({"ok": True})


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
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "items": json.loads(r["items"]),
                "total_cents": r["total_cents"],
                "status": r["status"],
                "created_at": r["created_at"],
                "customer_name": r["customer_name"] or "",
                "customer_address": r["customer_address"] or "",
                "customer_city": r["customer_city"] or "",
                "customer_department": r["customer_department"] or "",
                "delivery_method": r["delivery_method"] or "",
            }
        )
    return jsonify(out)


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
    "info_pagos": "Aceptamos tarjetas de débito y crédito de forma segura.",
    "info_envios": "Ropa 100% americana. Hacemos envíos por correo a todo Honduras. 🇭🇳",
}


@app.route("/api/info")
def api_info():
    """Información pública de la tienda: nombre, eslogan y secciones (pagos, envíos, etc.)."""
    out = {"store_name": get_setting("store_name", "Mi Tienda"), "currency": get_setting("currency", "hnl")}
    for k, default in INFO_DEFAULTS.items():
        v = (get_setting(k) or "").strip()
        out[k] = v or default
    return jsonify(out)


@app.route("/api/admin/settings", methods=["GET"])
@login_required
def admin_get_settings():
    out = {
        "store_name": get_setting("store_name", "Mi Tienda de Ropa"),
        "currency": get_setting("currency", "hnl"),
        "tagline": get_setting("tagline", INFO_DEFAULTS["tagline"]),
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
        set_setting("store_name", (data["store_name"] or "").strip() or "Mi Tienda de Ropa")
    if "currency" in data:
        cur = (data["currency"] or "hnl").strip().lower()
        set_setting("currency", cur if len(cur) == 3 else "hnl")
    for k in ["tagline", "info_horarios", "info_ubicacion",
              "info_contacto", "info_pagos", "info_envios"]:
        if k in data:
            set_setting(k, (data[k] or "").strip())
    for k in ["stripe_secret_key", "stripe_publishable_key", "stripe_webhook_secret"]:
        if k in data and data[k]:
            set_setting(k, data[k].strip())
    return jsonify({"ok": True})


# ---------------- Checkout con Stripe ----------------
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
    data = request.get_json(force=True, silent=True) or {}
    customer = data.get("customer") or {}
    name = (customer.get("name") or "").strip()
    address = (customer.get("address") or "").strip()
    city = (customer.get("city") or "").strip()
    department = (customer.get("department") or "").strip()
    delivery = (customer.get("delivery") or "").strip()
    if not name or not address or not city or not department:
        return (
            jsonify(
                {
                    "error": "Completa tu nombre, dirección, ciudad y departamento "
                    "para poder hacer el envío."
                }
            ),
            400,
        )
    if delivery not in ("domicilio", "oficina"):
        return (
            jsonify(
                {
                    "error": "Elige el método de entrega: envío a domicilio "
                    "o recoger en oficina cercana."
                }
            ),
            400,
        )
    secret = get_setting("stripe_secret_key")
    if not secret:
        return (
            jsonify(
                {
                    "error": "Los pagos con tarjeta aún no están configurados. "
                    "El dueño de la tienda debe agregar su clave de Stripe en los ajustes."
                }
            ),
            400,
        )
    data = request.get_json(force=True, silent=True) or {}
    try:
        line_items, total, snapshot = _validate_cart(data.get("items"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO orders(items, total_cents, status, created_at, "
        "customer_name, customer_address, customer_city, customer_department, "
        "delivery_method) VALUES(?,?, 'pending', ?,?,?,?,?,?)",
        (
            json.dumps(snapshot),
            total,
            int(time.time()),
            name,
            address,
            city,
            department,
            delivery,
        ),
    )
    order_id = cur.lastrowid
    db.commit()

    stripe.api_key = secret
    base_url = os.environ.get("BASE_URL", request.host_url.rstrip("/"))
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=f"{base_url}/exito?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/cancelado",
            metadata={
                "order_id": str(order_id),
                "cliente": name[:120],
                "entrega": delivery,
                "ciudad": f"{city}, {department}"[:120],
            },
        )
    except Exception as e:
        db.execute("UPDATE orders SET status='error' WHERE id=?", (order_id,))
        db.commit()
        return jsonify({"error": f"No se pudo iniciar el pago: {e}"}), 502

    db.execute(
        "UPDATE orders SET stripe_session_id=? WHERE id=?",
        (checkout_session.id, order_id),
    )
    db.commit()
    return jsonify({"url": checkout_session.url, "order_id": order_id})


def _finalize_order(order_id):
    """Marca el pedido como pagado y descuenta el stock. Idempotente."""
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row or row["status"] == "paid":
        return
    items = json.loads(row["items"])
    for it in items:
        db.execute(
            "UPDATE products SET stock = MAX(0, stock - ?) WHERE id=?",
            (it["qty"], it["id"]),
        )
    db.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,))
    db.commit()


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
