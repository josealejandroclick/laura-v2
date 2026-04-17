"""
Tool: ghl_agendar_cita
Consulta slots disponibles en GHL y agenda la cita en el slot mas cercano
a la hora solicitada por el cliente.
Horario disponible: lunes a viernes, 10am a 7pm ET.
"""

import json
import os
import logging
import httpx
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TZ_ET = ZoneInfo("America/New_York")
except ImportError:
    TZ_ET = None

logger = logging.getLogger("ghl_agendar_cita")

GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_CALENDAR_ID = os.getenv("GHL_CALENDAR_ID", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"

TOOL_SCHEMA = {
    "name": "ghl_agendar_cita",
    "description": (
        "Agenda una cita en el calendario de GHL para que un asesor llame al cliente. "
        "Horario disponible: lunes a viernes de 10am a 7pm hora del este (ET). "
        "Si el cliente pide antes de las 10am, ofrece 10am. Si pide despues de las 7pm, ofrece el dia siguiente."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "contacto_id": {
                "type": "string",
                "description": "ID del contacto en GHL (obtenido de ghl_registrar_contacto)"
            },
            "nombre": {"type": "string", "description": "Nombre del cliente"},
            "telefono": {"type": "string", "description": "Telefono del cliente"},
            "fecha_hora_iso": {
                "type": "string",
                "description": "Fecha y hora solicitada por el cliente en formato ISO: 2026-04-08T15:00:00. Debe ser entre 10am y 7pm ET, lunes a viernes."
            },
            "notas": {"type": "string", "description": "Notas para el asesor sobre el lead"}
        },
        "required": ["nombre", "telefono", "fecha_hora_iso"]
    }
}


def _ahora_et() -> datetime:
    if TZ_ET:
        return datetime.now(TZ_ET)
    return datetime.utcnow()


def _dt_et(fecha_hora_iso: str) -> datetime:
    """Parsea un ISO string y lo convierte a ET."""
    try:
        dt = datetime.fromisoformat(fecha_hora_iso)
        if dt.tzinfo is None and TZ_ET:
            dt = dt.replace(tzinfo=TZ_ET)
        return dt
    except Exception:
        return _ahora_et()


def _obtener_slots_disponibles(fecha: datetime, dias_busqueda: int = 3) -> list:
    """
    Consulta los slots disponibles en GHL para los proximos dias_busqueda dias.
    Retorna lista de slots en formato ISO con timezone.
    """
    if not GHL_API_KEY or not GHL_CALENDAR_ID:
        return []

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-04-15",
        "Content-Type": "application/json"
    }

    # Rango de busqueda: desde la fecha solicitada hasta dias_busqueda dias despues
    if TZ_ET:
        start_dt = fecha.replace(hour=0, minute=0, second=0, microsecond=0)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=TZ_ET)
        end_dt = start_dt + timedelta(days=dias_busqueda)
    else:
        start_dt = fecha.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(days=dias_busqueda)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    try:
        r = httpx.get(
            f"{GHL_BASE_URL}/calendars/{GHL_CALENDAR_ID}/free-slots",
            headers=headers,
            params={
                "startDate": start_ms,
                "endDate": end_ms,
                "timezone": "America/New_York"
            },
            timeout=10
        )
        logger.info(f"[SLOTS] Status: {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            slots = []
            for fecha_key, valor in data.items():
                if fecha_key == "traceId":
                    continue
                if isinstance(valor, dict) and "slots" in valor:
                    slots.extend(valor["slots"])
            logger.info(f"[SLOTS] Encontrados {len(slots)} slots disponibles")
            return slots
        else:
            logger.error(f"[SLOTS] Error GHL {r.status_code}: {r.text[:300]}")
            return []
    except Exception as e:
        logger.error(f"[SLOTS] Excepcion: {e}")
        return []


def _encontrar_slot_mas_cercano(slots: list, hora_deseada: datetime) -> str:
    """
    Encuentra el slot mas cercano a la hora deseada.
    Retorna el slot como string ISO o vacio si no hay slots.
    """
    if not slots:
        return ""

    mejor_slot = None
    menor_diff = None

    for slot_str in slots:
        try:
            # Parsear slot con timezone
            if "+" in slot_str or (slot_str.count("-") > 2):
                slot_dt = datetime.fromisoformat(slot_str)
            else:
                slot_dt = datetime.fromisoformat(slot_str)
                if TZ_ET:
                    slot_dt = slot_dt.replace(tzinfo=TZ_ET)

            # Solo slots en el futuro
            ahora = _ahora_et()
            if slot_dt.tzinfo and ahora.tzinfo:
                if slot_dt < ahora:
                    continue
            
            # Normalizar para comparar
            if hora_deseada.tzinfo and slot_dt.tzinfo:
                diff = abs((slot_dt - hora_deseada).total_seconds())
            else:
                diff = abs((slot_dt.replace(tzinfo=None) - hora_deseada.replace(tzinfo=None)).total_seconds())

            if menor_diff is None or diff < menor_diff:
                menor_diff = diff
                mejor_slot = slot_str
        except Exception as e:
            logger.warning(f"[SLOTS] Error parseando slot {slot_str}: {e}")
            continue

    return mejor_slot or ""


def ejecutar(nombre: str, telefono: str, fecha_hora_iso: str,
             contacto_id: str = "", notas: str = "", **kwargs) -> str:

    if not GHL_API_KEY or not GHL_CALENDAR_ID:
        return json.dumps({"exito": False, "error": "GHL no configurado — verificar GHL_API_KEY y GHL_CALENDAR_ID"})

    try:
        # Parsear la hora deseada
        dt_deseado = _dt_et(fecha_hora_iso)
        hora = dt_deseado.hour
        dia_semana = dt_deseado.weekday()

        # Validar que sea dia habil
        if dia_semana >= 5:
            return json.dumps({
                "exito": False,
                "error": "Solo se puede agendar lunes a viernes.",
                "sugerencia": "Ofrece el proximo lunes."
            })

        # Ajustar hora si esta fuera de rango
        if hora < 10:
            dt_deseado = dt_deseado.replace(hour=10, minute=0, second=0)
        elif hora >= 19:
            # Siguiente dia habil a las 10am
            dt_deseado = (dt_deseado + timedelta(days=1)).replace(hour=10, minute=0, second=0)
            while dt_deseado.weekday() >= 5:
                dt_deseado = dt_deseado + timedelta(days=1)

        # Consultar slots disponibles
        slots = _obtener_slots_disponibles(dt_deseado, dias_busqueda=4)

        if not slots:
            return json.dumps({
                "exito": False,
                "error": "No hay slots disponibles en el calendario para ese rango de fechas.",
                "sugerencia": "Escala al asesor para coordinar la cita manualmente."
            })

        # Encontrar el slot mas cercano a la hora deseada
        slot_elegido = _encontrar_slot_mas_cercano(slots, dt_deseado)

        if not slot_elegido:
            return json.dumps({
                "exito": False,
                "error": "No se encontro un slot valido disponible.",
                "sugerencia": "Escala al asesor para coordinar la cita manualmente."
            })

        logger.info(f"[AGENDAR] Slot elegido: {slot_elegido} (deseado: {fecha_hora_iso})")

        # Calcular end time (45 minutos despues del slot)
        slot_dt = datetime.fromisoformat(slot_elegido)
        slot_end = slot_dt + timedelta(minutes=45)
        slot_end_str = slot_end.isoformat()

        # Crear el appointment en GHL
        headers = {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Version": "2021-04-15",
            "Content-Type": "application/json"
        }

        payload = {
            "calendarId": GHL_CALENDAR_ID,
            "locationId": GHL_LOCATION_ID,
            "startTime": slot_elegido,
            "endTime": slot_end_str,
            "title": f"Llamada con {nombre} - Sara Bot",
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

        logger.info(f"[AGENDAR] Respuesta GHL: {r.status_code} — {r.text[:300]}")

        if r.status_code in (200, 201):
            data = r.json()
            apt_id = data.get("id") or data.get("event", {}).get("id", "")

            # Formatear hora para mostrar al cliente
            slot_display = slot_dt.strftime("%d/%m/%Y a las %I:%M %p")

            # Mover oportunidad a stage "Cita agendada" (best effort)
            if contacto_id:
                try:
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
                    logger.warning(f"[AGENDAR] No se pudo mover oportunidad: {e}")

            return json.dumps({
                "exito": True,
                "appointment_id": apt_id,
                "fecha_hora": slot_display,
                "slot_original": slot_elegido,
                "mensaje": f"Cita agendada para {nombre} el {slot_display}."
            }, ensure_ascii=False)

        else:
            return json.dumps({
                "exito": False,
                "error": f"GHL respondio {r.status_code}: {r.text[:300]}"
            })

    except Exception as e:
        logger.error(f"[AGENDAR] Excepcion: {e}", exc_info=True)
        return json.dumps({"exito": False, "error": str(e)})
