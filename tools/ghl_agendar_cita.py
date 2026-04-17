"""
Tool: ghl_agendar_cita
Consulta slots disponibles en GHL y agenda la cita en el slot mas cercano
a la hora solicitada por el cliente.
Horario disponible: lunes a viernes, 10am a 7pm ET.
Incluye conversion de zona horaria del cliente a ET para la notificacion al equipo.
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

# Mapeo de estados a zonas horarias y offset vs ET
# offset_vs_et: cuantas horas hay que SUMAR para convertir hora local a ET
# PT = ET - 3, MT = ET - 2, CT = ET - 1, ET = ET
ESTADO_TIMEZONE = {
    # Pacific Time (PT) — ET - 3
    "CA": ("PT", 3), "WA": ("PT", 3), "OR": ("PT", 3),
    "NV": ("PT", 3), "AZ": ("PT", 3),
    # Mountain Time (MT) — ET - 2
    "MT": ("MT", 2), "WY": ("MT", 2), "CO": ("MT", 2),
    "UT": ("MT", 2), "NM": ("MT", 2), "ID": ("MT", 2),
    # Central Time (CT) — ET - 1
    "TX": ("CT", 1), "OK": ("CT", 1), "KS": ("CT", 1),
    "NE": ("CT", 1), "SD": ("CT", 1), "ND": ("CT", 1),
    "MN": ("CT", 1), "IA": ("CT", 1), "MO": ("CT", 1),
    "AR": ("CT", 1), "LA": ("CT", 1), "WI": ("CT", 1),
    "IL": ("CT", 1), "MS": ("CT", 1), "AL": ("CT", 1),
    "TN": ("CT", 1), "OK": ("CT", 1),
    # Eastern Time (ET) — sin diferencia
    "FL": ("ET", 0), "GA": ("ET", 0), "NC": ("ET", 0),
    "SC": ("ET", 0), "VA": ("ET", 0), "NY": ("ET", 0),
    "PA": ("ET", 0), "OH": ("ET", 0), "MI": ("ET", 0),
    "IN": ("ET", 0), "KY": ("ET", 0), "WV": ("ET", 0),
    "MD": ("ET", 0), "DC": ("ET", 0), "DE": ("ET", 0),
    "NJ": ("ET", 0), "CT": ("ET", 0), "RI": ("ET", 0),
    "MA": ("ET", 0), "VT": ("ET", 0), "NH": ("ET", 0),
    "ME": ("ET", 0), "NY": ("ET", 0),
}

NOMBRE_ZONA = {
    "PT": "hora del Pacifico",
    "MT": "hora de Montana",
    "CT": "hora del Centro",
    "ET": "hora del Este"
}

TOOL_SCHEMA = {
    "name": "ghl_agendar_cita",
    "description": (
        "Agenda una cita en el calendario de GHL para que un asesor llame al cliente. "
        "Horario disponible: lunes a viernes de 10am a 7pm hora del este (ET). "
        "Si el cliente pide antes de las 10am, ofrece 10am. Si pide despues de las 7pm, ofrece el dia siguiente. "
        "Incluir el estado del cliente si se conoce para mostrar la hora correcta al equipo."
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
                "description": "Fecha y hora solicitada por el cliente en formato ISO: 2026-04-08T15:00:00. Si el cliente esta en otra zona horaria, convertir primero a ET antes de pasar aqui."
            },
            "estado": {
                "type": "string",
                "description": "Abreviatura del estado donde vive el cliente, ej: CA, TX, FL, NY. Usado para mostrar la hora local del cliente en la notificacion al equipo."
            },
            "hora_local_cliente": {
                "type": "string",
                "description": "Hora que el cliente pidio en su zona horaria local, ej: 10:00am. Solo incluir si es diferente a ET."
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


def _calcular_hora_et_desde_local(hora_local_str: str, estado: str, fecha_base: datetime) -> str:
    """
    Dado una hora local del cliente y su estado, devuelve la hora ET equivalente.
    Retorna string formateado para la notificacion.
    """
    if not estado or not hora_local_str:
        return ""

    estado_upper = estado.upper().strip()
    if estado_upper not in ESTADO_TIMEZONE:
        return ""

    zona_abrev, offset_horas = ESTADO_TIMEZONE[estado_upper]
    if offset_horas == 0:
        return ""  # Ya es ET, no hay diferencia

    nombre_zona = NOMBRE_ZONA.get(zona_abrev, zona_abrev)
    return f"Cliente: {hora_local_str} ({nombre_zona}) = {offset_horas}h mas tarde en ET (Doral)"


def _obtener_slots_disponibles(fecha: datetime, dias_busqueda: int = 4) -> list:
    """Consulta los slots disponibles en GHL."""
    if not GHL_API_KEY or not GHL_CALENDAR_ID:
        return []

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-04-15",
        "Content-Type": "application/json"
    }

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
            logger.info(f"[SLOTS] Encontrados {len(slots)} slots")
            return slots
        else:
            logger.error(f"[SLOTS] Error GHL {r.status_code}: {r.text[:300]}")
            return []
    except Exception as e:
        logger.error(f"[SLOTS] Excepcion: {e}")
        return []


def _encontrar_slot_mas_cercano(slots: list, hora_deseada: datetime) -> str:
    """Encuentra el slot mas cercano a la hora deseada."""
    if not slots:
        return ""

    mejor_slot = None
    menor_diff = None

    for slot_str in slots:
        try:
            slot_dt = datetime.fromisoformat(slot_str)
            if slot_dt.tzinfo is None and TZ_ET:
                slot_dt = slot_dt.replace(tzinfo=TZ_ET)

            ahora = _ahora_et()
            if slot_dt.tzinfo and ahora.tzinfo:
                if slot_dt < ahora:
                    continue

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
             contacto_id: str = "", notas: str = "",
             estado: str = "", hora_local_cliente: str = "", **kwargs) -> str:

    if not GHL_API_KEY or not GHL_CALENDAR_ID:
        return json.dumps({"exito": False, "error": "GHL no configurado — verificar GHL_API_KEY y GHL_CALENDAR_ID"})

    try:
        dt_deseado = _dt_et(fecha_hora_iso)
        hora = dt_deseado.hour
        dia_semana = dt_deseado.weekday()

        if dia_semana >= 5:
            return json.dumps({
                "exito": False,
                "error": "Solo se puede agendar lunes a viernes.",
                "sugerencia": "Ofrece el proximo lunes."
            })

        if hora < 10:
            dt_deseado = dt_deseado.replace(hour=10, minute=0, second=0)
        elif hora >= 19:
            dt_deseado = (dt_deseado + timedelta(days=1)).replace(hour=10, minute=0, second=0)
            while dt_deseado.weekday() >= 5:
                dt_deseado = dt_deseado + timedelta(days=1)

        slots = _obtener_slots_disponibles(dt_deseado, dias_busqueda=4)

        if not slots:
            return json.dumps({
                "exito": False,
                "error": "No hay slots disponibles en el calendario para ese rango de fechas.",
                "sugerencia": "Escala al asesor para coordinar la cita manualmente."
            })

        slot_elegido = _encontrar_slot_mas_cercano(slots, dt_deseado)

        if not slot_elegido:
            return json.dumps({
                "exito": False,
                "error": "No se encontro un slot valido disponible.",
                "sugerencia": "Escala al asesor para coordinar la cita manualmente."
            })

        logger.info(f"[AGENDAR] Slot elegido: {slot_elegido} (deseado: {fecha_hora_iso})")

        slot_dt = datetime.fromisoformat(slot_elegido)
        slot_end = slot_dt + timedelta(minutes=45)
        slot_end_str = slot_end.isoformat()

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

        # Construir notas con info de zona horaria
        notas_completas = notas or ""
        estado_upper = estado.upper().strip() if estado else ""
        info_zona = ""

        if estado_upper and estado_upper in ESTADO_TIMEZONE:
            zona_abrev, offset_horas = ESTADO_TIMEZONE[estado_upper]
            slot_et_display = slot_dt.strftime("%d/%m/%Y a las %I:%M %p ET")

            if offset_horas > 0:
                # Calcular hora local del cliente
                slot_local = slot_dt - timedelta(hours=offset_horas)
                slot_local_display = slot_local.strftime("%I:%M %p")
                nombre_zona = NOMBRE_ZONA.get(zona_abrev, zona_abrev)
                info_zona = (
                    f"ZONA HORARIA: Cliente en {estado_upper} ({nombre_zona}). "
                    f"Cita: {slot_local_display} {zona_abrev} = {slot_dt.strftime('%I:%M %p')} ET"
                )
            else:
                info_zona = f"Cliente en {estado_upper} (ET — misma zona que Doral)"
        elif hora_local_cliente:
            info_zona = f"Hora solicitada por cliente: {hora_local_cliente}"

        if info_zona:
            notas_completas = f"{info_zona}\n{notas_completas}".strip()

        if notas_completas:
            payload["notes"] = notas_completas

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

            slot_et_display = slot_dt.strftime("%d/%m/%Y a las %I:%M %p ET")

            # Construir mensaje con ambas horas si hay diferencia de zona
            if estado_upper and estado_upper in ESTADO_TIMEZONE:
                zona_abrev, offset_horas = ESTADO_TIMEZONE[estado_upper]
                if offset_horas > 0:
                    slot_local = slot_dt - timedelta(hours=offset_horas)
                    nombre_zona = NOMBRE_ZONA.get(zona_abrev, zona_abrev)
                    mensaje_cita = (
                        f"Cita agendada para {nombre}. "
                        f"Cliente: {slot_local.strftime('%I:%M %p')} {zona_abrev} ({nombre_zona}) — "
                        f"Equipo llama a las: {slot_dt.strftime('%I:%M %p')} ET (Doral). "
                        f"Fecha: {slot_dt.strftime('%d/%m/%Y')}."
                    )
                else:
                    mensaje_cita = f"Cita agendada para {nombre} el {slot_et_display}."
            else:
                mensaje_cita = f"Cita agendada para {nombre} el {slot_et_display}."

            # Mover oportunidad a stage Cita agendada (best effort)
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
                "fecha_hora_et": slot_et_display,
                "slot_original": slot_elegido,
                "mensaje": mensaje_cita
            }, ensure_ascii=False)

        else:
            return json.dumps({
                "exito": False,
                "error": f"GHL respondio {r.status_code}: {r.text[:300]}"
            })

    except Exception as e:
        logger.error(f"[AGENDAR] Excepcion: {e}", exc_info=True)
        return json.dumps({"exito": False, "error": str(e)})
