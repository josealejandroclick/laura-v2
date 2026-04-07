"""
Tool: ghl_registrar_contacto
Crea o actualiza un contacto en GHL CRM.
"""

import json
import os
import httpx

GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"
GHL_HEADERS = {
    "Authorization": f"Bearer {GHL_API_KEY}",
    "Version": "2021-07-28",
    "Content-Type": "application/json"
}

TOOL_SCHEMA = {
    "name": "ghl_registrar_contacto",
    "description": "Crea o actualiza un contacto en el CRM de GHL con los datos del lead.",
    "input_schema": {
        "type": "object",
        "properties": {
            "nombre": {"type": "string", "description": "Nombre completo del cliente"},
            "telefono": {"type": "string", "description": "Número de teléfono con código de país, ej: +13051234567"},
            "email": {"type": "string", "description": "Email del cliente (opcional)"},
            "zip": {"type": "string", "description": "Código postal"},
            "ciudad": {"type": "string", "description": "Ciudad"},
            "notas": {"type": "string", "description": "Notas adicionales sobre el lead"},
            "fuente": {"type": "string", "description": "Fuente del lead: organico, ad_dental, ad_embarazadas, etc."}
        },
        "required": ["nombre", "telefono"]
    }
}


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
        # Buscar si el contacto ya existe por teléfono
        busqueda = httpx.get(
            f"{GHL_BASE_URL}/contacts/search",
            headers=headers,
            params={"locationId": GHL_LOCATION_ID, "phone": telefono},
            timeout=10
        )

        contacto_id = None
        if busqueda.status_code == 200:
            data = busqueda.json()
            contactos = data.get("contacts", [])
            if contactos:
                contacto_id = contactos[0].get("id")

        # Armar payload
        nombre_parts = nombre.strip().split(" ", 1)
        payload = {
            "locationId": GHL_LOCATION_ID,
            "firstName": nombre_parts[0],
            "lastName": nombre_parts[1] if len(nombre_parts) > 1 else "",
            "phone": telefono,
            "source": fuente,
            "tags": [fuente, "sara-bot"],
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
            # Actualizar contacto existente
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

        if r.status_code in (200, 201):
            data = r.json()
            cid = data.get("contact", {}).get("id") or contacto_id
            return json.dumps({
                "exito": True,
                "contacto_id": cid,
                "accion": "actualizado" if contacto_id else "creado",
                "mensaje": f"Contacto {nombre} registrado en GHL."
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "exito": False,
                "error": f"GHL respondió {r.status_code}: {r.text[:200]}"
            })

    except Exception as e:
        return json.dumps({"exito": False, "error": str(e)})
