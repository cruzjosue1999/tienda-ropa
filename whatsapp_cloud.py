#!/usr/bin/env python3
"""Avisos automáticos de pedidos por WhatsApp (Cloud API oficial de Meta).

Este módulo envía las notificaciones de estado de pedido de "Tu Nuevo Estilo"
usando la Plataforma de WhatsApp Business (Cloud API). Solo usa la librería
estándar (urllib), sin dependencias nuevas.

Configuración (variables de entorno en el servidor):
  WHATSAPP_TOKEN            Token permanente de un System User de Meta con los
                            permisos whatsapp_business_messaging y
                            whatsapp_business_management. Nunca va en el código.
  WHATSAPP_PHONE_NUMBER_ID  ID del número de teléfono registrado en la
                            Plataforma de WhatsApp Business (el que envía).

Si alguna de las dos variables falta, send_order_update() no hace nada y
devuelve False: la tienda sigue funcionando igual, solo sin avisos automáticos.

Plantillas sugeridas (categoría "utility", se crean y aprueban en el panel de
Meta > WhatsApp > Plantillas de mensajes):

  1) pedido_confirmado  (utility)
     Texto sugerido:
       "¡Hola {{1}}! Tu pedido #{{2}} de *Tu Nuevo Estilo* fue confirmado ✅
        y ya está en proceso de empaquetamiento 📦. Te avisaremos por aquí
        cuando tu paquete esté listo."
     Parámetros del cuerpo: {{1}} = nombre del cliente, {{2}} = número de pedido.

  2) pedido_listo  (utility)
     Texto sugerido:
       "¡Hola {{1}}! Tu paquete del pedido #{{2}} de *Tu Nuevo Estilo* ya está
        envuelto y listo 🎁. La compañía de envíos lo recogerá pronto.
        {{3}}"
     Parámetros del cuerpo: {{1}} = nombre del cliente, {{2}} = número de
     pedido, {{3}} = "Número de guía: XXXXX." (opcional, solo si hay guía).

Nota: el cliente debe haber aceptado recibir avisos (casilla de WhatsApp en el
checkout -> columna whatsapp_optin de orders). La app solo llama a este módulo
cuando whatsapp_optin=1.
"""

import json
import os
import urllib.request
import urllib.error

TEMPLATE_CONFIRMADO = "pedido_confirmado"
TEMPLATE_LISTO = "pedido_listo"
API_VERSION = "v23.0"


def _config():
    token = (os.environ.get("WHATSAPP_TOKEN") or "").strip()
    phone_id = (os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    if not token or not phone_id:
        return None
    return token, phone_id


def normalize_phone(raw):
    """Normaliza un teléfono hondureño a formato E.164 (ej: 50488887777).

    Acepta 8 dígitos locales, con o sin prefijo 504/+504, guiones y espacios.
    Devuelve None si no es un número hondureño válido.
    """
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    if len(digits) == 11 and digits.startswith("504"):
        digits = digits[3:]
    if len(digits) == 8:
        return "504" + digits
    return None


def send_order_update(to_phone, customer_name, order_id, kind, tracking_number=None):
    """Envía un aviso de pedido por WhatsApp con una plantilla aprobada.

    kind: 'confirmado' -> plantilla pedido_confirmado
          'listo'      -> plantilla pedido_listo (con guía si se indica)
    Devuelve True si Meta aceptó el mensaje, False en cualquier otro caso
    (sin credenciales, número inválido, error de red o de la API).
    Nunca lanza excepciones: un fallo aquí no debe romper el flujo del pedido.
    """
    try:
        cfg = _config()
        if cfg is None:
            return False
        token, phone_id = cfg
        to = normalize_phone(to_phone)
        if not to:
            return False
        name = (customer_name or "").strip() or "cliente"

        if kind == "confirmado":
            template = TEMPLATE_CONFIRMADO
            params = [name, str(order_id)]
        elif kind == "listo":
            template = TEMPLATE_LISTO
            params = [name, str(order_id)]
            if tracking_number and str(tracking_number).strip():
                params.append("Número de guía: %s." % str(tracking_number).strip())
            else:
                params.append("")
        else:
            return False

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": "es"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": p} for p in params
                        ],
                    }
                ],
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://graph.facebook.com/%s/%s/messages" % (API_VERSION, phone_id),
            data=data,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        return bool(body.get("messages"))
    except Exception:
        return False
