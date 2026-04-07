"""
Tool: ghl_enviar_mensaje
Envía un mensaje WhatsApp via GHL a un contacto.
Usado para follow-ups y notificaciones desde Sara.
"""

import json
import os
import httpx

GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"

TOOL_SCHEMA = {
    "name": "ghl_enviar_mensaje",
    "description": "Envía un mensaje WhatsApp a un contacto via GHL. Usar para follow-ups cuando el cliente no responde.",
    "input_schema": {
        "type": "object",
        "properties": {
            "contacto_id": {
                "type": "string",
                "description": "ID del contacto en GHL"
            },
            "mensaje": {
                "type": "string",
                "description": "Texto del mensaje a enviar"
            },
            "tipo": {
                "type": "string",
                "description": "Tipo de mensaje: WhatsApp o SMS",
                "enum": ["WhatsApp", "SMS"],
                "default": "WhatsApp"
            }
        },
        "required": ["contacto_id", "mensaje"]
    }
}


def ejecutar(contacto_id: str, mensaje: str, tipo: str = "WhatsApp", **kwargs) -> str:
    if not GHL_API_KEY:
        return json.dumps({"exito": False, "error": "GHL no configurado"})

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-04-15",
        "Content-Type": "application/json"
    }

    try:
        # Buscar conversación activa del contacto
        conv_r = httpx.get(
            f"{GHL_BASE_URL}/conversations/search",
            headers=headers,
            params={"locationId": GHL_LOCATION_ID, "contactId": contacto_id},
            timeout=10
        )

        conversation_id = None
        if conv_r.status_code == 200:
            convs = conv_r.json().get("conversations", [])
            if convs:
                conversation_id = convs[0].get("id")

        if not conversation_id:
            return json.dumps({
                "exito": False,
                "error": "No se encontró conversación activa para este contacto."
            })

        # Enviar mensaje
        r = httpx.post(
            f"{GHL_BASE_URL}/conversations/messages",
            headers=headers,
            json={
                "type": tipo,
                "conversationId": conversation_id,
                "message": mensaje
            },
            timeout=10
        )

        if r.status_code in (200, 201):
            return json.dumps({
                "exito": True,
                "mensaje": f"Mensaje enviado via {tipo}."
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "exito": False,
                "error": f"GHL respondió {r.status_code}: {r.text[:200]}"
            })

    except Exception as e:
        return json.dumps({"exito": False, "error": str(e)})
