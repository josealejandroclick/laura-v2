"""
Tool: registrar_equity
Guarda el lead en Supabase (tabla leads_equity) y crea/actualiza contacto en GHL Equity.
Deduplica por email o teléfono.
"""

import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOL_SCHEMA = {
    "name": "registrar_equity",
    "description": (
        "Registra un lead calificado del Programa EQUITY en Supabase y en GoHighLevel (subcuenta Equity). "
        "Deduplica por email o teléfono. Asigna los tags correspondientes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "first_name": {
                "type": "string",
                "description": "Nombre del candidato."
            },
            "last_name": {
                "type": "string",
                "description": "Apellido del candidato."
            },
            "email": {
                "type": "string",
                "description": "Correo electrónico."
            },
            "phone": {
                "type": "string",
                "description": "Teléfono con código de país si está disponible."
            },
            "estado_usa": {
                "type": "string",
                "description": "Estado de EE.UU. donde vive el candidato."
            },
            "como_se_entero": {
                "type": "string",
                "description": "Fuente: 'publicidad', 'instagram', 'facebook', 'invitado'.",
                "enum": ["publicidad", "instagram", "facebook", "invitado", ""]
            },
            "referido_por": {
                "type": "string",
                "description": "Nombre de quien lo invitó (solo si como_se_entero es 'invitado')."
            },
            "tiene_licencia": {
                "type": "boolean",
                "description": "True si tiene licencia de seguros."
            },
            "tipo_licencia": {
                "type": "string",
                "description": "Tipo de licencia: 215, 220, 240, 214, otra o vacío."
            },
            "tag_principal": {
                "type": "string",
                "description": "Tag principal asignado por calificar_lead."
            },
            "tags_adicionales": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Lista de tags adicionales."
            },
            "origen": {
                "type": "string",
                "description": "Origen del lead.",
                "enum": ["whatsapp_laura", "ghl", "lovable"],
                "default": "whatsapp_laura"
            }
        },
        "required": ["first_name", "phone", "tag_principal"]
    }
}


def ejecutar(
    first_name: str,
    phone: str,
    tag_principal: str,
    last_name: str = "",
    email: str = "",
    estado_usa: str = "",
    como_se_entero: str = "",
    referido_por: str = "",
    tiene_licencia: bool = False,
    tipo_licencia: str = "",
    tags_adicionales: list = None,
    origen: str = "whatsapp_laura"
) -> str:
    """Guarda el lead en Supabase y GHL."""

    if tags_adicionales is None:
        tags_adicionales = []

    todos_los_tags = list(set([tag_principal] + tags_adicionales))
    resultados = {}

    # --- Supabase ---
    try:
        from supabase import create_client
        from config import SUPABASE_URL, SUPABASE_KEY

        if SUPABASE_URL and SUPABASE_KEY:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

            # Datos del lead
            lead_data = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "estado_usa": estado_usa,
                "como_se_entero": como_se_entero,
                "referido_por": referido_por,
                "tiene_licencia": tiene_licencia,
                "tipo_licencia": tipo_licencia,
                "tag_principal": tag_principal,
                "tags": todos_los_tags,
                "etapa_actual": "lead_calificado",
                "origen": origen,
            }

            # Buscar si ya existe por teléfono o email (deduplicación)
            existing = None
            if phone:
                res = supabase.table("leads_equity").select("id").eq("phone", phone).execute()
                if res.data:
                    existing = res.data[0]["id"]
            if not existing and email:
                res = supabase.table("leads_equity").select("id").eq("email", email).execute()
                if res.data:
                    existing = res.data[0]["id"]

            if existing:
                # Actualizar
                supabase.table("leads_equity").update(lead_data).eq("id", existing).execute()
                resultados["supabase"] = {"status": "actualizado", "id": existing}
            else:
                # Insertar nuevo
                res = supabase.table("leads_equity").insert(lead_data).execute()
                resultados["supabase"] = {"status": "creado", "data": res.data}
        else:
            resultados["supabase"] = {"status": "skip", "razon": "credenciales no configuradas"}

    except Exception as e:
        resultados["supabase"] = {"status": "error", "detalle": str(e)}

    # --- GHL ---
    try:
        import requests
        from config import GHL_API_KEY, GHL_LOCATION_ID

        if GHL_API_KEY and GHL_LOCATION_ID:
            headers = {
                "Authorization": f"Bearer {GHL_API_KEY}",
                "Content-Type": "application/json",
                "Version": "2021-07-28"
            }

            contacto_data = {
                "firstName": first_name,
                "lastName": last_name,
                "email": email,
                "phone": phone,
                "locationId": GHL_LOCATION_ID,
                "tags": todos_los_tags,
                "customField": {
                    "estado_usa": estado_usa,
                    "como_se_entero": como_se_entero,
                    "referido_por": referido_por,
                    "tipo_licencia": tipo_licencia,
                }
            }

            # Intentar buscar contacto existente por teléfono
            search_url = f"https://services.leadconnectorhq.com/contacts/search/duplicate"
            search_payload = {"locationId": GHL_LOCATION_ID, "phone": phone}
            search_res = requests.post(search_url, json=search_payload, headers=headers, timeout=10)

            if search_res.status_code == 200 and search_res.json().get("contact"):
                # Actualizar existente
                contact_id = search_res.json()["contact"]["id"]
                update_url = f"https://services.leadconnectorhq.com/contacts/{contact_id}"
                requests.put(update_url, json=contacto_data, headers=headers, timeout=10)
                resultados["ghl"] = {"status": "actualizado", "contact_id": contact_id}
            else:
                # Crear nuevo
                create_url = "https://services.leadconnectorhq.com/contacts/"
                res = requests.post(create_url, json=contacto_data, headers=headers, timeout=10)
                if res.status_code in (200, 201):
                    contact_id = res.json().get("contact", {}).get("id", "")
                    resultados["ghl"] = {"status": "creado", "contact_id": contact_id}
                else:
                    resultados["ghl"] = {"status": "error", "codigo": res.status_code, "detalle": res.text[:200]}
        else:
            resultados["ghl"] = {"status": "skip", "razon": "credenciales no configuradas"}

    except Exception as e:
        resultados["ghl"] = {"status": "error", "detalle": str(e)}

    return json.dumps({
        "exito": True,
        "nombre": f"{first_name} {last_name}".strip(),
        "tag_asignado": tag_principal,
        "todos_los_tags": todos_los_tags,
        "resultados": resultados
    }, ensure_ascii=False)
