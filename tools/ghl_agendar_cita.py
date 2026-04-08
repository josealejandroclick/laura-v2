"""
Tool: ghl_agendar_cita
Busca slots disponibles y agenda una cita en GHL Calendar.
Horario disponible: lunes a viernes, 10am a 7pm ET.
"""

import json
import os
import httpx
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TZ_ET = ZoneInfo("America/New_York")
except ImportError:
    TZ_ET = None

GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_CALENDAR_ID = os.getenv("GHL_CALENDAR_ID", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"

TOOL_SCHEMA = {
    "name": "ghl_agendar_cita",
    "description": (
        "Agenda una cita en el calendario de GHL para que un asesor llame al cliente. "
        "Horario disponible: lunes a viernes de 10am a 7pm hora del este (ET). "
        "Si el cliente pide antes de las 10am, ofrece 10am. Si pide después de las 7pm, ofrece el día siguiente."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "contacto_id": {
                "type": "string",
                "description": "ID del contacto en GHL (obtenido de ghl_registrar_contacto)"
            },
            "nombre": {"type": "string", "description": "Nombre del cliente"},
            "telefono": {"type": "string", "description": "Teléfono del cliente"},
            "fecha_hora_iso": {
                "type": "string",
                "description": "Fecha y hora de la cita en formato ISO: 2026-04-08T15:00:00. Debe ser entre 10am y 7pm ET, lunes a viernes."
            },
            "notas": {"type": "string", "description": "Notas para el asesor sobre el lead"}
        },
        "required": ["nombre", "telefono", "fecha_hora_iso"]
    }
}


def _et_to_utc(fecha_hora_iso: str) -> str:
    """Convierte hora ET a UTC para la API de GHL."""
    try:
        dt_naive = datetime.fromisoformat(fecha_hora_iso)
        if TZ_ET:
            dt_et = dt_naive.replace(tzinfo=TZ_ET)
        else:
            # Fallback: asumir ET = UTC-4 (EDT) o UTC-5 (EST)
            dt_et = dt_naive.replace(tzinfo=timezone(timedelta(hours=-4)))
        dt_utc = dt_et.astimezone(timezone.utc)
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    except Exception:
        return fecha_hora_iso


def ejecutar(nombre: str, telefono: str, fecha_hora_iso: str,
             contacto_id: str = "", notas: str = "", **kwargs) -> str:
    if not GHL_API_KEY or not GHL_CALENDAR_ID:
        return json.dumps({"exito": False, "error": "GHL no configurado"})

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-04-15",
        "Content-Type": "application/json"
    }

    try:
        # Validar horario 10am-7pm ET
        dt = datetime.fromisoformat(fecha_hora_iso)
        hora = dt.hour
        dia_semana = dt.weekday()  # 0=lunes, 6=domingo

        if dia_semana >= 5:
            return json.dumps({
                "exito": False,
                "error": "Solo se puede agendar lunes a viernes.",
                "sugerencia": "Ofrece el próximo lunes."
            })

        if hora < 10:
            return json.dumps({
                "exito": False,
                "error": "Horario no disponible antes de las 10am ET.",
                "sugerencia": "Ofrece las 10am del mismo día."
            })

        if hora >= 19:
            return json.dumps({
                "exito": False,
                "error": "Horario no disponible después de las 7pm ET.",
                "sugerencia": "Ofrece el siguiente día hábil a las 10am."
            })

        # Convertir a UTC
        start_utc = _et_to_utc(fecha_hora_iso)
        dt_end = dt + timedelta(minutes=45)
        end_utc = _et_to_utc(dt_end.strftime("%Y-%m-%dT%H:%M:%S"))

        # Crear appointment
        payload = {
            "calendarId": GHL_CALENDAR_ID,
            "locationId": GHL_LOCATION_ID,
            "startTime": start_utc,
            "endTime": end_utc,
            "title": f"Llamada con {nombre} — Sara Bot",
            "appointmentStatus": "confirmed",
            "phone": telefono,
        }

        if contacto_id:
            payload["contactId"] = contacto_id

        if notas:
            payload["notes"] = notas

        r = httpx.post(
            f"{GHL_BASE_URL}/calendars/events/appointments",
            headers=headers,
            json=payload,
            timeout=10
        )

if r.status_code in (200, 201):
            data = r.json()
            apt_id = data.get("id") or data.get("event", {}).get("id", "")
            hora_display = dt.strftime("%d/%m/%Y a las %I:%M %p")

            # Mover oportunidad a stage "Cita agendada" (best effort)
            if contacto_id:
                try:
                    # Buscar oportunidad activa del contacto
                    r_ops = httpx.get(
                        f"{GHL_BASE_URL}/contacts/{contacto_id}/opportunities",
                        headers=headers,
                        timeout=10
                    )
                    if r_ops.status_code == 200:
                        ops = r_ops.json().get("opportunities", [])
                        if ops:
                            opp_id = ops[0].get("id", "")
                            if opp_id:
                                httpx.put(
                                    f"{GHL_BASE_URL}/opportunities/{opp_id}",
                                    headers=headers,
                                    json={"pipelineStageId": "62262740-c73b-4be1-a0bb-145af5b62709"},
                                    timeout=10
                                )
                except Exception as e:
                    import logging
                    logging.getLogger("ghl_agendar_cita").warning(f"No se pudo mover oportunidad: {e}")

            return json.dumps({
                "exito": True,
                "appointment_id": apt_id,
                "fecha_hora": hora_display,
                "mensaje": f"Cita agendada para {nombre} el {hora_display}."
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "exito": False,
                "error": f"GHL respondió {r.status_code}: {r.text[:300]}"
            })

    except Exception as e:
        return json.dumps({"exito": False, "error": str(e)})
