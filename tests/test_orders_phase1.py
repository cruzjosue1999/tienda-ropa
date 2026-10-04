"""Pruebas fase 1: teléfono/ID en checkout, estados de pedido, rastreo y WhatsApp.

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

# Sin Turso en pruebas: BD local temporal.
for var in ("TURSO_URL", "TURSO_TOKEN"):
    os.environ.pop(var, None)

spec = importlib.util.spec_from_file_location(
    "tienda_app", os.path.join(MIGRATION_DIR, "app.py")
)
ta = importlib.util.module_from_spec(spec)
sys.modules["tienda_app"] = ta
spec.loader.exec_module(ta)

import whatsapp_cloud  # noqa: E402  (viene de MIGRATION_DIR vía sys.path)


@pytest.fixture()
def client():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    ta.DB_PATH = tmp.name
    # En producción app.py vive en la raíz junto a templates/; en pruebas el
    # módulo se importa desde turso-migration/, así que se apunta la carpeta.
    ta.app.template_folder = os.path.join(BASE, "templates")
    with ta.app.app_context():
        ta.init_db()
        db = sqlite3.connect(tmp.name)
        db.execute(
            "INSERT INTO products(name, price_cents, stock, active, created_at)"
            " VALUES('Camisa', 25000, 10, 1, 1)"
        )
        db.commit()
        db.close()
    ta.app.config["TESTING"] = True
    c = ta.app.test_client()
    # Crea la contraseña de admin y deja la sesión iniciada.
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


def checkout(c, customer, items=None):
    return c.post(
        "/api/checkout",
        json={
            "items": items or [{"id": 1, "qty": 1}],
            "customer": customer,
        },
    )


def order_row(db_path, order_id):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    db.close()
    return row


# ---------- Validadores ----------
def test_validators():
    assert ta.valid_phone("99998888")
    assert ta.valid_phone("+504 9999-8888")
    assert ta.valid_phone("50499998888")
    assert not ta.valid_phone("9999888")      # 7 dígitos
    assert not ta.valid_phone("999988889")    # 9 dígitos
    assert not ta.valid_phone("")
    # La identidad ya no se pide ni se valida en el checkout.


# ---------- Checkout ----------
def test_checkout_requiere_telefono(client):
    c, _ = client
    r = checkout(c, base_customer(phone=""))
    assert r.status_code == 400
    assert "celular" in r.get_json()["error"]


def test_checkout_telefono_invalido(client):
    c, _ = client
    r = checkout(c, base_customer(phone="12345"))
    assert r.status_code == 400


def test_checkout_solo_recoger_y_deposito(client):
    """Política local (por mientras): solo recoger en persona;
    pago con depósito o efectivo."""
    c, _ = client
    r = checkout(c, base_customer())
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["order_id"]
    assert d["shipping_cents"] == 0
    r = checkout(c, base_customer(payment="efectivo"))
    assert r.status_code == 200, r.get_json()
    # domicilio queda rechazado
    r = checkout(c, base_customer(delivery="domicilio"))
    assert r.status_code == 400


def test_checkout_identidad_ignorada_si_llega(client):
    """Si un cliente viejo envía id_number, se ignora sin error."""
    c, db_path = client
    r = checkout(c, base_customer(id_number="0801199901234"))
    assert r.status_code == 200, r.get_json()
    row = order_row(db_path, r.get_json()["order_id"])
    assert row["customer_id_number"] == ""  # columna sin uso


def test_checkout_oficina_sin_identidad_ok(client):
    c, _ = client
    r = checkout(c, base_customer(delivery="oficina"))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["order_id"]


def test_checkout_guarda_nuevos_campos(client):
    c, db_path = client
    r = checkout(
        c,
        base_customer(
            phone="+504 9999-8888",
            authorized_receiver="María López",
            whatsapp_optin=True,
        ),
    )
    assert r.status_code == 200, r.get_json()
    row = order_row(db_path, r.get_json()["order_id"])
    assert row["customer_phone"] == "99998888"          # normalizado
    assert row["authorized_receiver"] == "María López"
    assert row["whatsapp_optin"] == 1
    assert row["fulfillment_status"] == "pending"


def test_checkout_sin_persona_autorizada_guarda_vacio(client):
    c, db_path = client
    r = checkout(c, base_customer())
    assert r.status_code == 200, r.get_json()
    row = order_row(db_path, r.get_json()["order_id"])
    assert row["authorized_receiver"] == ""


def test_checkout_sin_optin_guarda_cero(client):
    c, db_path = client
    r = checkout(c, base_customer())
    row = order_row(db_path, r.get_json()["order_id"])
    assert row["whatsapp_optin"] == 0


# ---------- Transiciones de estado ----------
def test_marcar_pagado_pasa_a_empaquetamiento(client):
    c, db_path = client
    oid = checkout(c, base_customer()).get_json()["order_id"]
    assert c.post(f"/api/admin/orders/{oid}/paid").status_code == 200
    row = order_row(db_path, oid)
    assert row["status"] == "paid"
    assert row["fulfillment_status"] == "packing"


def test_status_endpoint_cambia_estado_y_guia(client):
    c, db_path = client
    oid = checkout(c, base_customer()).get_json()["order_id"]
    c.post(f"/api/admin/orders/{oid}/paid")
    r = c.post(
        f"/api/admin/orders/{oid}/status",
        json={"status": "ready", "tracking_number": "CX-12345"},
    )
    assert r.status_code == 200, r.get_json()
    row = order_row(db_path, oid)
    assert row["fulfillment_status"] == "ready"
    assert row["tracking_number"] == "CX-12345"


def test_status_endpoint_rechaza_estado_invalido(client):
    c, _ = client
    oid = checkout(c, base_customer()).get_json()["order_id"]
    r = c.post(f"/api/admin/orders/{oid}/status", json={"status": "volando"})
    assert r.status_code == 400


def test_status_endpoint_sin_login(client):
    # Sin contraseña (decisión del dueño 2026-09-30): funciona sin sesión.
    c, _ = client
    oid = checkout(c, base_customer()).get_json()["order_id"]
    anon = ta.app.test_client()
    r = anon.post(f"/api/admin/orders/{oid}/status", json={"status": "ready"})
    assert r.status_code == 200
    assert r.get_json()["fulfillment_status"] == "ready"


def test_no_pagado_vuelve_a_pending(client):
    c, db_path = client
    oid = checkout(c, base_customer()).get_json()["order_id"]
    c.post(f"/api/admin/orders/{oid}/paid")
    assert c.post(f"/api/admin/orders/{oid}/unpaid").status_code == 200
    row = order_row(db_path, oid)
    assert row["status"] == "pending"
    assert row["fulfillment_status"] == "pending"


def test_proteccion_auto_paid_sigue_vigente(client):
    c, _ = client
    oid = checkout(c, base_customer()).get_json()["order_id"]
    with ta.app.app_context():
        ta._finalize_order(oid, auto=True)
    r = c.delete(f"/api/admin/orders/{oid}")
    assert r.status_code == 403
    r = c.post(f"/api/admin/orders/{oid}/unpaid")
    assert r.status_code == 403


# ---------- Rastreo ----------
def test_track_ok(client):
    c, _ = client
    oid = checkout(c, base_customer()).get_json()["order_id"]
    c.post(f"/api/admin/orders/{oid}/paid")
    c.post(
        f"/api/admin/orders/{oid}/status",
        json={"status": "shipped", "tracking_number": "CX-999"},
    )
    r = c.get(f"/api/track?order={oid}&phone=+504 9999-8888")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["fulfillment_status"] == "shipped"
    assert d["tracking_number"] == "CX-999"


def test_track_telefono_incorrecto_404(client):
    c, _ = client
    oid = checkout(c, base_customer()).get_json()["order_id"]
    r = c.get(f"/api/track?order={oid}&phone=11112222")
    assert r.status_code == 404


def test_track_pedido_inexistente_404(client):
    c, _ = client
    r = c.get("/api/track?order=99999&phone=99998888")
    assert r.status_code == 404


def test_pagina_rastrear_existe(client):
    c, _ = client
    r = c.get("/rastrear")
    assert r.status_code == 200
    assert "Rastrear" in r.get_data(as_text=True)


def test_admin_list_incluye_nuevos_campos(client):
    c, _ = client
    checkout(c, base_customer(phone="99998888"))
    orders = c.get("/api/admin/orders").get_json()
    o = orders[0]
    for campo in (
        "customer_phone",
        "authorized_receiver",
        "whatsapp_optin",
        "fulfillment_status",
        "tracking_number",
    ):
        assert campo in o, campo
    assert o["customer_phone"] == "99998888"


def test_admin_list_muestra_persona_autorizada(client):
    c, _ = client
    checkout(c, base_customer(authorized_receiver="María López"))
    orders = c.get("/api/admin/orders").get_json()
    assert orders[0]["authorized_receiver"] == "María López"


# ---------- WhatsApp Cloud ----------
def test_whatsapp_sin_credenciales_no_falla():
    for var in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"):
        os.environ.pop(var, None)
    assert whatsapp_cloud.send_order_update("99998888", "Juan", 1, "confirmado") is False
    assert whatsapp_cloud.send_order_update("99998888", "Juan", 1, "listo", "CX-1") is False
    assert whatsapp_cloud.send_order_update("99998888", "Juan", 1, "otro") is False


def test_whatsapp_normalize_phone():
    assert whatsapp_cloud.normalize_phone("99998888") == "50499998888"
    assert whatsapp_cloud.normalize_phone("+504 9999-8888") == "50499998888"
    assert whatsapp_cloud.normalize_phone("50499998888") == "50499998888"
    assert whatsapp_cloud.normalize_phone("123") is None
    assert whatsapp_cloud.normalize_phone("") is None
