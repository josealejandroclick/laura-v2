"""
Tool: verificar_zip
Verifica un ZIP code o busca el ZIP de una ciudad usando Google Maps API.
Devuelve ciudad y estado para que Laura pueda determinar el estado del candidato.
"""

import json
import os
import httpx

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


TOOL_SCHEMA = {
    "name": "verificar_zip",
    "description": (
        "Verifica un código ZIP o busca el estado a partir de una ciudad. "
        "Usar siempre que el candidato mencione una ciudad o ZIP para determinar "
        "en qué estado de EE.UU. se encuentra, sin tener que preguntarle."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "zipcode": {
                "type": "string",
                "description": "Código ZIP de 5 dígitos a verificar. Dejar vacío si solo tienes ciudad."
            },
            "ciudad": {
                "type": "string",
                "description": "Nombre de la ciudad a buscar. Usar cuando el cliente no sabe su ZIP."
            },
            "estado": {
                "type": "string",
                "description": "Código de estado de 2 letras (ej: FL, TX, CA). Ayuda a precisar la búsqueda."
            }
        }
    }
}


def _zip_a_ciudad(zipcode: str) -> dict:
    """Convierte un ZIP code a ciudad y estado via Google Maps."""
    try:
        params = {
            "address": f"{zipcode}, USA",
            "key": GOOGLE_MAPS_API_KEY,
            "components": f"country:US|postal_code:{zipcode}"
        }
        r = httpx.get(GEOCODE_URL, params=params, timeout=8)
        if r.status_code != 200:
            return {"error": "No se pudo conectar con el servicio de mapas"}

        results = r.json().get("results", [])
        if not results:
            return {"error": f"ZIP {zipcode} no encontrado"}

        components = results[0].get("address_components", [])
        city = ""
        state = ""

        for comp in components:
            types = comp.get("types", [])
            if "locality" in types:
                city = comp.get("long_name", "")
            elif "sublocality_level_1" in types and not city:
                city = comp.get("long_name", "")
            elif "neighborhood" in types and not city:
                city = comp.get("long_name", "")
            elif "administrative_area_level_1" in types:
                state = comp.get("short_name", "")
            elif "administrative_area_level_2" in types and not city:
                city = comp.get("long_name", "").replace(" County", "")

        if not city and not state:
            return {"error": f"ZIP {zipcode} no tiene información de ciudad"}

        return {"zipcode": zipcode, "ciudad": city, "estado": state}

    except Exception as e:
        return {"error": f"Error verificando ZIP: {str(e)}"}


def _ciudad_a_zip(ciudad: str, estado: str = "FL") -> dict:
    """Busca el ZIP principal de una ciudad via Google Maps."""
    try:
        params = {
            "address": f"{ciudad}, {estado}, USA",
            "key": GOOGLE_MAPS_API_KEY
        }
        r = httpx.get(GEOCODE_URL, params=params, timeout=8)
        if r.status_code != 200:
            return {"error": "No se pudo conectar con el servicio de mapas"}

        for result in r.json().get("results", []):
            for comp in result.get("address_components", []):
                if "postal_code" in comp.get("types", []):
                    z = comp.get("long_name", "")
                    if z and len(z) == 5:
                        return {"zipcode": z, "ciudad": ciudad, "estado": estado}

        return {"error": f"No se encontró ZIP para {ciudad}, {estado}"}

    except Exception as e:
        return {"error": f"Error buscando ciudad: {str(e)}"}


def ejecutar(zipcode: str = "", ciudad: str = "", estado: str = "FL") -> str:
    """
    Verifica un ZIP o busca el ZIP de una ciudad.
    Prioriza zipcode si viene, sino busca por ciudad.
    """
    if not GOOGLE_MAPS_API_KEY:
        # Fallback sin API key — devolver datos básicos para no bloquear el flujo
        if zipcode:
            return json.dumps({
                "exito": True,
                "zipcode": zipcode,
                "ciudad": "tu área",
                "estado": estado,
                "nota": "Verificación de mapas no disponible"
            }, ensure_ascii=False)
        return json.dumps({
            "exito": False,
            "error": "Se necesita el ZIP code. ¿Podrías decirme tu código ZIP?"
        }, ensure_ascii=False)

    if zipcode and len(zipcode) == 5 and zipcode.isdigit():
        resultado = _zip_a_ciudad(zipcode)
    elif ciudad:
        resultado = _ciudad_a_zip(ciudad, estado or "FL")
    else:
        return json.dumps({
            "exito": False,
            "error": "Necesito el ZIP o la ciudad para verificar"
        }, ensure_ascii=False)

    if "error" in resultado:
        return json.dumps({
            "exito": False,
            "error": resultado["error"]
        }, ensure_ascii=False)

    return json.dumps({
        "exito": True,
        "zipcode": resultado.get("zipcode", zipcode),
        "ciudad": resultado.get("ciudad", ""),
        "estado": resultado.get("estado", estado)
    }, ensure_ascii=False)
