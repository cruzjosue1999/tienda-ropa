"""Pruebas: notificaciones push (Web Push / VAPID) de nuevos pedidos.

Se ejecutan contra una BD SQLite temporal (sin Turso). El envío real se
mockea: nunca se hace red. Uso:
    ~/workspace/tienda-ropa-sistema/venv/bin/python -m pytest tests/test_push.py -v
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

from pywebpush import WebPushException  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    ta.DB_PATH = tmp.name
    ta.app.template_folder = os.path.join(BASE, "templates")
    ta._PUSH_SYNC = True  # envío síncrono en tests (sin hilos)
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    with ta.app.app_context():
        ta.init_db()
        # init_db idempotente: segunda corrida no debe fallar
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


def make_order(c):
    return c.post(
        "/api/checkout",
        json={"customer": base_customer(), "items": [{"id": 1, "qty": 1}]},
    )


def subscribe(c, endpoint="https://push.test/sub1"):
    return c.post(
        "/api/admin/push/subscribe",
        json={
            "endpoint": endpoint,
            "keys": {"p256dh": "p256dh-falso", "auth": "auth-falso"},
        },
    )


def test_push_endpoints_sin_login(client):
    # Sin contraseña (decisión del dueño 2026-09-30): ningún endpoint push da 401.
    c, _ = client
    anon = ta.app.test_client()  # sin sesión de admin
    assert anon.post("/api/admin/push/subscribe", json={}).status_code == 400  # incompleta, no 401
    assert anon.post("/api/admin/push/unsubscribe", json={}).status_code == 200
    assert anon.get("/api/admin/push/status").status_code == 200
    assert anon.get("/api/admin/vapid-public-key").status_code == 200


def test_push_subscribe_y_status(client):
    c, _ = client
    r = subscribe(c)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["ok"] is True
    r = c.get("/api/admin/push/status")
    assert r.status_code == 200
    assert r.get_json()["subscriptions"] == 1
    # Re-suscribir el mismo endpoint actualiza, no duplica
    assert subscribe(c).status_code == 200
    assert c.get("/api/admin/push/status").get_json()["subscriptions"] == 1


def test_push_subscribe_incompleta_400(client):
    c, _ = client
    r = c.post("/api/admin/push/subscribe", json={"endpoint": "https://x"})
    assert r.status_code == 400
    r = c.post(
        "/api/admin/push/subscribe",
        json={"endpoint": "https://x", "keys": {"p256dh": "a"}},
    )
    assert r.status_code == 400


def test_push_unsubscribe(client):
    c, _ = client
    assert subscribe(c).status_code == 200
    r = c.post(
        "/api/admin/push/unsubscribe", json={"endpoint": "https://push.test/sub1"}
    )
    assert r.status_code == 200
    assert c.get("/api/admin/push/status").get_json()["subscriptions"] == 0


def test_vapid_public_key_devuelve_env(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "CLAVE-PUBLICA-FAKE")
    r = c.get("/api/admin/vapid-public-key")
    assert r.status_code == 200
    assert r.get_json()["public_key"] == "CLAVE-PUBLICA-FAKE"
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    assert c.get("/api/admin/vapid-public-key").get_json()["public_key"] == ""


def test_checkout_sin_vapid_no_intenta_push(client, monkeypatch):
    c, _ = client
    calls = []
    monkeypatch.setattr(ta, "_pywebpush_send", lambda *a, **k: calls.append((a, k)))
    assert subscribe(c).status_code == 200
    r = make_order(c)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert calls == []  # sin claves VAPID no se intenta nada


def test_checkout_dispara_push(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "PUB")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "PRIV")
    calls = []
    monkeypatch.setattr(ta, "_pywebpush_send", lambda *a, **k: calls.append((a, k)))
    assert subscribe(c).status_code == 200
    r = make_order(c)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert len(calls) == 1
    sub_info, payload = calls[0][0][0], calls[0][0][1]
    assert sub_info["endpoint"] == "https://push.test/sub1"
    data = json.loads(payload)
    order_id = r.get_json()["order_id"]
    assert data["title"] == "🧾 Nuevo pedido #%d" % order_id
    assert "Juan Pérez" in data["body"]
    assert "Depósito" in data["body"]
    assert data["tag"] == "pedido-%d" % order_id


def test_checkout_sigue_aunque_push_falle(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "PUB")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "PRIV")

    def boom(*a, **k):
        raise RuntimeError("red caída")

    monkeypatch.setattr(ta, "_pywebpush_send", boom)
    assert subscribe(c).status_code == 200
    r = make_order(c)
    # El pedido se crea igual aunque el push falle
    assert r.status_code == 200, r.get_data(as_text=True)
    assert c.get("/api/admin/push/status").get_json()["subscriptions"] == 1


def test_push_suscripcion_muerta_se_borra(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "PUB")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "PRIV")

    class FakeResp:
        status_code = 410

    def gone(*a, **k):
        raise WebPushException("gone", response=FakeResp())

    monkeypatch.setattr(ta, "_pywebpush_send", gone)
    assert subscribe(c).status_code == 200
    r = make_order(c)
    assert r.status_code == 200
    # La suscripción muerta (410) se eliminó
    assert c.get("/api/admin/push/status").get_json()["subscriptions"] == 0
