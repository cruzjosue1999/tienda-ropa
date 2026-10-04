"""Pruebas: foto de portada por ítem en el endpoint de pedidos del admin.

Se ejecutan contra una BD SQLite temporal (sin Turso). Uso:
    ~/workspace/tienda-ropa-sistema/venv/bin/python -m pytest tests/ -v
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_DIR = os.path.join(BASE, "turso-migration")
sys.path.insert(0, MIGRATION_DIR)

for var in ("TURSO_URL", "TURSO_TOKEN"):
    os.environ.pop(var, None)

spec = importlib.util.spec_from_file_location(
    "tienda_app", os.path.join(MIGRATION_DIR, "app.py")
)
ta = importlib.util.module_from_spec(spec)
sys.modules["tienda_app"] = ta
spec.loader.exec_module(ta)

import whatsapp_cloud  # noqa: E402  (viene de MIGRATION_DIR vía sys.path)

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # bytes mínimos con firma JPEG


@pytest.fixture()
def client():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    ta.DB_PATH = tmp.name
    ta.app.template_folder = os.path.join(BASE, "templates")
    with ta.app.app_context():
        ta.init_db()
        db = sqlite3.connect(tmp.name)
        # Producto 1: CON foto de portada.
        db.execute(
            "INSERT INTO products(name, price_cents, stock, active, created_at,"
            " photo_blob, photo_mime)"
            " VALUES('Camisa', 25000, 10, 1, 1, ?, 'image/jpeg')",
            (FAKE_JPEG,),
        )
        # Producto 2: SIN foto.
        db.execute(
            "INSERT INTO products(name, price_cents, stock, active, created_at)"
            " VALUES('Pantalón', 30000, 10, 1, 2)"
        )
        db.commit()
        db.close()
    ta.app.config["TESTING"] = True
    c = ta.app.test_client()
    r = c.post("/api/setup", json={"password": "secreta123"})
    assert r.status_code == 200, r.get_data(as_text=True)
    yield c, tmp.name
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def base_customer(**kw):
    c = {
        "name": "Juan Pérez",
        "address": "Col. Centro",
        "city": "Trujillo",
        "department": "Colón",
        "delivery": "oficina",
        "payment": "deposito",
        "phone": "99998888",
    }
    c.update(kw)
    return c


def test_admin_orders_incluye_foto_portada(client):
    c, _ = client
    r = c.post(
        "/api/checkout",
        json={"customer": base_customer(), "items": [{"id": 1, "qty": 2}]},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    r = c.get("/api/admin/orders")
    assert r.status_code == 200
    orders = r.get_json()
    assert len(orders) == 1
    item = orders[0]["items"][0]
    assert item["photo_url"] == "/api/photo/1"
    assert item["qty"] == 2
    assert item["name"] == "Camisa"


def test_admin_orders_sin_foto_devuelve_null(client):
    c, _ = client
    r = c.post(
        "/api/checkout",
        json={"customer": base_customer(), "items": [{"id": 2, "qty": 1}]},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    r = c.get("/api/admin/orders")
    item = r.get_json()[0]["items"][0]
    assert item["photo_url"] is None


def test_admin_orders_producto_borrado_devuelve_null(client):
    c, dbpath = client
    r = c.post(
        "/api/checkout",
        json={"customer": base_customer(), "items": [{"id": 1, "qty": 1}]},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    db = sqlite3.connect(dbpath)
    db.execute("DELETE FROM products WHERE id=1")
    db.commit()
    db.close()
    r = c.get("/api/admin/orders")
    item = r.get_json()[0]["items"][0]
    assert item["photo_url"] is None
    # El resto del ítem sigue intacto (snapshot del pedido).
    assert item["name"] == "Camisa"


def test_admin_orders_sin_login(client):
    # Sin contraseña (decisión del dueño 2026-09-30): el admin responde sin 401.
    c, _ = client
    c2 = ta.app.test_client()  # sin sesión
    r = c2.get("/api/admin/orders")
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)
