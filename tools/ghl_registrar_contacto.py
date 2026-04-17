"""
Tool: ghl_registrar_contacto
Crea o actualiza un contacto en GHL CRM.
"""

import json
import os
import logging
import httpx

logger = logging.getLogger("ghl_registrar_contacto")

GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"

TOOL_SCHEMA = {
    "name": "ghl_registrar_contacto",
    "description": (
        "Crea o actualiza un contacto en el CRM de GHL con los datos del lead. "
        "Llamar siempre que el cliente de su nombre real para actualizar el contacto en GHL. "
        "Tambien llamar al momento del cierre con todos los datos recopilados."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "nombre": {"type": "string", "description": "Nombre completo real del cliente"},
            "telefono": {"type": "string", "description": "Numero de telefono con codigo de pais, ej: +13051234567"},
            "email": {"type": "string", "description": "Email del cliente (opcional)"},
            "zip": {"type": "string", "description": "Codigo postal"},
            "ciudad": {"type": "string", "description": "Ciudad"},
            "notas": {"type": "string", "description": "Notas adicionales sobre el lead"},
            "fuente": {"type": "string", "description": "Fuente del lead: organico, ad_dental, ad_embarazadas, etc."}
        },
        "required": ["nombre", "telefono"]
    }
}


def _limpiar_telefono(telefono: str) -> str:
    """Extrae los ultimos 10 digitos del telefono para busqueda."""
    limpio = telefono.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    return limpio[-10:] if len(limpio) > 10 else limpio


def ejecutar(nombre: str, telefono: str, email: str = "", zip: str = "",
             ciudad: str = "", notas: str = "", fuente: str = "organico", **kwargs) -> str:
    if not GHL_API_KEY or not GHL_LOCATION_ID:
        return json.dumps({"exito": False, "error": "GHL no configurado"})

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-07-28",
        "Content-Type": "application/json"
    }

    try:
        # Buscar contacto existente por telefono
        contacto_id = None
        telefono_query = _limpiar_telefono(telefono)

        busqueda = httpx.get(
            f"{GHL_BASE_URL}/contacts/",
            headers=headers,
            params={"locationId": GHL_LOCATION_ID, "query": telefono_query},
            timeout=10
        )

        if busqueda.status_code == 200:
            contactos = busqueda.json().get("contacts", [])
            if contactos:
                contacto_id = contactos[0].get("id")
                logger.info(f"[GHL] Contacto existente encontrado: {contacto_id}")
        else:
            logger.warning(f"[GHL] Error buscando contacto {telefono_query}: {busqueda.status_code}")

        # Armar payload con nombre real del cliente
        nombre_parts = nombre.strip().split(" ", 1)
        payload = {
            "locationId": GHL_LOCATION_ID,
            "firstName": nombre_parts[0],
            "lastName": nombre_parts[1] if len(nombre_parts) > 1 else "",
            "phone": telefono,
            "source": fuente,
            "tags": [fuente, "sara-bot", "lead_whatsapp"],
        }
        if email:
            payload["email"] = email
        if zip:
            payload["postalCode"] = zip
        if ciudad:
            payload["city"] = ciudad
        if notas:
            payload["customFields"] = [{"key": "notas_sara", "value": notas}]

        if contacto_id:
            # Actualizar contacto existente — incluye nombre real
            r = httpx.put(
                f"{GHL_BASE_URL}/contacts/{contacto_id}",
                headers=headers,
                json=payload,
                timeout=10
            )
        else:
            # Crear contacto nuevo
            r = httpx.post(
                f"{GHL_BASE_URL}/contacts/",
                headers=headers,
                json=payload,
                timeout=10
            )

        logger.info(f"[GHL] Registrar contacto {nombre}: {r.status_code}")

        if r.status_code in (200, 201):
            data = r.json()
            cid = data.get("contact", {}).get("id") or contacto_id or ""
            return json.dumps({
                "exito": True,
                "contacto_id": cid,
                "accion": "actualizado" if contacto_id else "creado",
                "mensaje": f"Contacto {nombre} registrado en GHL."
            }, ensure_ascii=False)
        else:
            logger.error(f"[GHL] Error {r.status_code}: {r.text[:200]}")
            return json.dumps({
                "exito": False,
                "error": f"GHL respondio {r.status_code}: {r.text[:200]}"
            })

    except Exception as e:
        logger.error(f"[GHL] Excepcion: {e}")
        return json.dumps({"exito": False, "error": str(e)})
