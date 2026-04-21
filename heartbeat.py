"""
SAM -- Heartbeat + Cron (Agente Proactivo)

Follow-up con intervalos variables:
  #1 -> 30 minutos
  #2 -> 2 horas
  #3 -> 24 horas
  #4 -> 48 horas

Follow-ups de WhatsApp se envian via GHL automaticamente.
Antes de cada follow-up verifica tags del contacto:
- humano_activo -> detener seguimiento
- p_cliente_cerrado / cliente_dental -> detener seguimiento
- p_cita_agendada -> no vender, solo recordatorio 30 min antes de la cita
"""

import json
import os
import time
import threading
import logging
import httpx
from pathlib import Path
from datetime import datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger("sam_heartbeat")

# ============================================================
# CONFIGURACION
# ============================================================

HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "300"))  # 5 min

_FOLLOWUP_INTERVALS_RAW = os.getenv("FOLLOWUP_INTERVALS", "30,120,1440,2880")
FOLLOWUP_INTERVALS = [int(x) for x in _FOLLOWUP_INTERVALS_RAW.split(",")]
MAX_FOLLOWUPS = len(FOLLOWUP_INTERVALS)

DATA_DIR = os.getenv("SESSIONS_DIR", "data/sessions")
CRON_FILE = os.path.join(os.getenv("SESSIONS_DIR", "data"), "cron_tasks.json")
FOLLOWUP_FILE = os.path.join(os.getenv("SESSIONS_DIR", "data"), "followup_tracker.json")

GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"

# Tags que detienen el seguimiento completamente
TAGS_DETENER = {"humano_activo", "p_cliente_cerrado", "cliente_dental"}

# Tag que cambia el seguimiento a recordatorio de cita
TAG_CITA_AGENDADA = "p_cita_agendada"

# Control para recordatorios ya enviados (evitar duplicados)
_recordatorios_enviados = set()


# ============================================================
# FOLLOW-UP TRACKER
# ============================================================

def _cargar_tracker() -> dict:
    try:
        with open(FOLLOWUP_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _guardar_tracker(tracker: dict):
    Path(os.path.dirname(FOLLOWUP_FILE)).mkdir(parents=True, exist_ok=True)
    with open(FOLLOWUP_FILE, "w") as f:
        json.dump(tracker, f, indent=2, ensure_ascii=False)


def registrar_actividad(session_id: str):
    tracker = _cargar_tracker()
    tracker[session_id] = {
        "ultimo_mensaje": time.time(),
        "followups_enviados": 0,
        "activo": True
    }
    _guardar_tracker(tracker)


def obtener_leads_para_followup() -> list:
    tracker = _cargar_tracker()
    ahora = time.time()
    leads = []

    for session_id, data in tracker.items():
        if not data.get("activo", True):
            continue

        enviados = data.get("followups_enviados", 0)
        if enviados >= MAX_FOLLOWUPS:
            continue

        ultimo = data.get("ultimo_mensaje", ahora)
        minutos_transcurridos = (ahora - ultimo) / 60

        if enviados < len(FOLLOWUP_INTERVALS):
            minutos_requeridos = FOLLOWUP_INTERVALS[enviados]
            if minutos_transcurridos >= minutos_requeridos:
                if session_id.startswith("whatsapp_"):
                    canal = "whatsapp"
                elif str(session_id).lstrip("-").isdigit():
                    canal = "telegram"
                else:
                    continue
                leads.append((session_id, enviados + 1, canal))

    return leads


def marcar_followup_enviado(session_id: str):
    tracker = _cargar_tracker()
    if session_id in tracker:
        enviados = tracker[session_id].get("followups_enviados", 0) + 1
        tracker[session_id]["followups_enviados"] = enviados
        tracker[session_id]["ultimo_followup"] = time.time()
        if enviados >= MAX_FOLLOWUPS:
            tracker[session_id]["activo"] = False
        _guardar_tracker(tracker)


def desactivar_sesion(session_id: str):
    tracker = _cargar_tracker()
    if session_id in tracker:
        tracker[session_id]["activo"] = False
        _guardar_tracker(tracker)


# ============================================================
# GHL — VERIFICACION DE TAGS Y CITAS
# ============================================================

def _extraer_telefono_whatsapp(session_id: str) -> str:
    if session_id.startswith("whatsapp_"):
        return session_id[len("whatsapp_"):]
    return ""


def _buscar_datos_contacto_ghl(telefono: str) -> dict:
    """
    Busca contacto en GHL y retorna:
    { contacto_id, conversation_id, tags, proxima_cita_iso }
    """
    resultado = {
        "contacto_id": "",
        "conversation_id": "",
        "tags": [],
        "proxima_cita_iso": ""
    }

    if not GHL_API_KEY or not GHL_LOCATION_ID or not telefono:
        return resultado

    telefono_limpio = telefono.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    telefono_query = telefono_limpio[-10:] if len(telefono_limpio) > 10 else telefono_limpio

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-04-15",
        "Content-Type": "application/json"
    }

    try:
        # Buscar contacto
        r = httpx.get(
            f"{GHL_BASE_URL}/contacts/",
            headers=headers,
            params={"locationId": GHL_LOCATION_ID, "query": telefono_query},
            timeout=8
        )
        if r.status_code == 200:
            contactos = r.json().get("contacts", [])
            if not contactos:
                return resultado

            contacto = contactos[0]
            contacto_id = contacto.get("id", "")
            resultado["contacto_id"] = contacto_id
            resultado["tags"] = contacto.get("tags", [])

            # Buscar conversacion
            if contacto_id:
                conv_r = httpx.get(
                    f"{GHL_BASE_URL}/conversations/search",
                    headers=headers,
                    params={"locationId": GHL_LOCATION_ID, "contactId": contacto_id},
                    timeout=8
                )
                if conv_r.status_code == 200:
                    convs = conv_r.json().get("conversations", [])
                    if convs:
                        resultado["conversation_id"] = convs[0].get("id", "")

                # Buscar proxima cita si tiene tag p_cita_agendada
                if TAG_CITA_AGENDADA in resultado["tags"]:
                    apt_r = httpx.get(
                        f"{GHL_BASE_URL}/contacts/{contacto_id}/appointments",
                        headers=headers,
                        timeout=8
                    )
                    if apt_r.status_code == 200:
                        events = apt_r.json().get("events", [])
                        ahora_iso = datetime.utcnow().isoformat()
                        # Buscar cita futura mas proxima
                        futuras = [
                            e for e in events
                            if e.get("startTime", "") > ahora_iso
                            and e.get("appointmentStatus") == "confirmed"
                            and not e.get("deleted", False)
                        ]
                        if futuras:
                            futuras.sort(key=lambda x: x.get("startTime", ""))
                            resultado["proxima_cita_iso"] = futuras[0].get("startTime", "")

    except Exception as e:
        logger.warning(f"[FOLLOWUP-GHL] Error buscando datos de {telefono_query}: {e}")

    return resultado


def _debe_enviar_recordatorio_cita(cita_iso: str, session_id: str) -> bool:
    """
    Verifica si faltan entre 25 y 35 minutos para la cita.
    Evita enviar el recordatorio mas de una vez.
    """
    if not cita_iso or session_id in _recordatorios_enviados:
        return False

    try:
        # GHL devuelve UTC sin timezone
        cita_dt = datetime.fromisoformat(cita_iso.replace(" ", "T"))
        ahora_utc = datetime.utcnow()
        diff_minutos = (cita_dt - ahora_utc).total_seconds() / 60

        return 25 <= diff_minutos <= 35
    except Exception as e:
        logger.warning(f"[FOLLOWUP] Error calculando tiempo cita: {e}")
        return False


def _enviar_mensaje_ghl(conversation_id: str, contacto_id: str, mensaje: str) -> bool:
    """Envia un mensaje WhatsApp via GHL."""
    if not GHL_API_KEY or not conversation_id or not contacto_id:
        return False

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-04-15",
        "Content-Type": "application/json"
    }

    try:
        r = httpx.post(
            f"{GHL_BASE_URL}/conversations/messages",
            headers=headers,
            json={
                "type": "WhatsApp",
                "conversationId": conversation_id,
                "contactId": contacto_id,
                "message": mensaje
            },
            timeout=10
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"[FOLLOWUP-GHL] Excepcion enviando mensaje: {e}")
        return False


def _procesar_followup_whatsapp(session_id: str, followup_num: int) -> bool:
    """
    Procesa un follow-up de WhatsApp con logica inteligente:
    1. Verifica tags del contacto
    2. Si tiene tag de detener -> desactiva sesion
    3. Si tiene cita agendada -> envia recordatorio si toca
    4. Si no tiene cita -> envia follow-up de ventas normal
    """
    telefono = _extraer_telefono_whatsapp(session_id)
    if not telefono:
        return False

    datos = _buscar_datos_contacto_ghl(telefono)
    contacto_id = datos.get("contacto_id", "")
    conversation_id = datos.get("conversation_id", "")
    tags = set(datos.get("tags", []))
    proxima_cita = datos.get("proxima_cita_iso", "")

    # Verificar tags que detienen el seguimiento
    if tags & TAGS_DETENER:
        logger.info(f"[FOLLOWUP] {session_id} tiene tag de detener ({tags & TAGS_DETENER}) — desactivando")
        desactivar_sesion(session_id)
        return True  # No es un error, es intencional

    # Verificar si tiene cita agendada
    if TAG_CITA_AGENDADA in tags:
        if proxima_cita and _debe_enviar_recordatorio_cita(proxima_cita, session_id):
            mensaje = (
                "Hola! Te recordamos que en unos minutos te contacta nuestro asesor. "
                "Queda atenta a la llamada 😊"
            )
            if contacto_id and conversation_id:
                enviado = _enviar_mensaje_ghl(conversation_id, contacto_id, mensaje)
                if enviado:
                    _recordatorios_enviados.add(session_id)
                    desactivar_sesion(session_id)
                    logger.info(f"[FOLLOWUP] Recordatorio de cita enviado a {telefono}")
                    return True
        else:
            # Tiene cita pero no es momento del recordatorio — no hacer nada
            logger.info(f"[FOLLOWUP] {session_id} tiene cita agendada — sin follow-up de ventas")
            # Si la cita ya paso, desactivar
            if proxima_cita:
                try:
                    cita_dt = datetime.fromisoformat(proxima_cita.replace(" ", "T"))
                    if datetime.utcnow() > cita_dt + timedelta(hours=1):
                        desactivar_sesion(session_id)
                except Exception:
                    pass
        return True  # No enviar follow-up de ventas

    # Sin tags especiales — enviar follow-up de ventas normal
    if not contacto_id or not conversation_id:
        logger.warning(f"[FOLLOWUP-GHL] No se encontro contacto/conversacion para {telefono}")
        return False

    mensaje = generar_mensaje_followup(followup_num)
    enviado = _enviar_mensaje_ghl(conversation_id, contacto_id, mensaje)

    if enviado:
        logger.info(f"[FOLLOWUP-GHL] Follow-up #{followup_num} enviado a {telefono}")
    else:
        logger.error(f"[FOLLOWUP-GHL] Error enviando follow-up a {telefono}")

    return enviado


# ============================================================
# CRON TASKS
# ============================================================

def _cargar_cron() -> list:
    try:
        with open(CRON_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _guardar_cron(tasks: list):
    Path(os.path.dirname(CRON_FILE)).mkdir(parents=True, exist_ok=True)
    with open(CRON_FILE, "w") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)


def programar_tarea(session_id: str, ejecutar_en: str, tipo: str,
                    descripcion: str, datos: dict = None):
    tasks = _cargar_cron()
    tasks.append({
        "id": f"cron_{int(time.time())}_{session_id[:8]}",
        "session_id": session_id,
        "ejecutar_en": ejecutar_en,
        "tipo": tipo,
        "descripcion": descripcion,
        "datos": datos or {},
        "creado": datetime.now().isoformat(),
        "ejecutado": False
    })
    _guardar_cron(tasks)


def obtener_tareas_pendientes() -> list:
    tasks = _cargar_cron()
    ahora = datetime.now().isoformat()
    return [t for t in tasks if not t.get("ejecutado", False) and t.get("ejecutar_en", "9999") <= ahora]


def marcar_tarea_ejecutada(task_id: str):
    tasks = _cargar_cron()
    for t in tasks:
        if t.get("id") == task_id:
            t["ejecutado"] = True
            t["ejecutado_en"] = datetime.now().isoformat()
    _guardar_cron(tasks)


# ============================================================
# TOOL SCHEMA
# ============================================================

TOOL_SCHEMA = {
    "name": "agendar_tarea",
    "description": (
        "Programa una tarea futura: recordatorio de llamada, follow-up, "
        "o cualquier accion que deba ejecutarse en una fecha/hora especifica."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ejecutar_en": {
                "type": "string",
                "description": "Fecha y hora en formato ISO: 2026-04-02T10:00:00"
            },
            "tipo": {
                "type": "string",
                "enum": ["followup", "recordatorio", "notificacion"],
                "description": "Tipo de tarea"
            },
            "descripcion": {
                "type": "string",
                "description": "Que hacer cuando llegue la hora"
            }
        },
        "required": ["ejecutar_en", "tipo", "descripcion"]
    }
}


def ejecutar_agendar(ejecutar_en: str, tipo: str, descripcion: str, **kwargs) -> str:
    session_id = kwargs.get("session_id", "unknown")
    programar_tarea(session_id, ejecutar_en, tipo, descripcion)
    return json.dumps({
        "exito": True,
        "mensaje": f"Tarea programada: {descripcion} para {ejecutar_en}"
    }, ensure_ascii=False)


# ============================================================
# HEARTBEAT ENGINE
# ============================================================

class Heartbeat:

    def __init__(self, on_followup: Callable = None, on_cron: Callable = None):
        self.on_followup = on_followup
        self.on_cron = on_cron
        self._timer: Optional[threading.Timer] = None
        self._running = False
        self.ciclos = 0

    def iniciar(self):
        self._running = True
        self._programar_siguiente()
        logger.info(
            f"Heartbeat iniciado (cada {HEARTBEAT_INTERVAL}s) | "
            f"Follow-ups: {FOLLOWUP_INTERVALS} min"
        )

    def detener(self):
        self._running = False
        if self._timer:
            self._timer.cancel()
        logger.info(f"Heartbeat detenido despues de {self.ciclos} ciclos.")

    def _programar_siguiente(self):
        if self._running:
            self._timer = threading.Timer(HEARTBEAT_INTERVAL, self._latido)
            self._timer.daemon = True
            self._timer.start()

    def _latido(self):
        self.ciclos += 1
        try:
            # Follow-ups
            leads = obtener_leads_para_followup()
            for session_id, followup_num, canal in leads:
                logger.info(f"Follow-up #{followup_num} para {session_id} [{canal}]")

                if canal == "whatsapp":
                    try:
                        enviado = _procesar_followup_whatsapp(session_id, followup_num)
                        if enviado:
                            marcar_followup_enviado(session_id)
                        else:
                            logger.warning(f"[FOLLOWUP] No se pudo enviar a {session_id}")
                    except Exception as e:
                        logger.error(f"Error en follow-up WhatsApp {session_id}: {e}")

                elif canal == "telegram":
                    if self.on_followup:
                        try:
                            self.on_followup(session_id, followup_num)
                            marcar_followup_enviado(session_id)
                        except Exception as e:
                            logger.error(f"Error en follow-up Telegram {session_id}: {e}")

            # Tareas cron
            tareas = obtener_tareas_pendientes()
            for tarea in tareas:
                logger.info(f"Ejecutando cron: {tarea.get('descripcion')}")
                if self.on_cron:
                    try:
                        self.on_cron(tarea)
                        marcar_tarea_ejecutada(tarea["id"])
                    except Exception as e:
                        logger.error(f"Error en cron {tarea['id']}: {e}")

            # Reportes automaticos
            try:
                from reports.daily_report import verificar_y_ejecutar
                verificar_y_ejecutar()
            except Exception as e:
                logger.error(f"Error en reportes: {e}")

            # Analizador semanal
            try:
                from reports.analyzer_report import verificar_y_ejecutar_analisis
                verificar_y_ejecutar_analisis()
            except Exception as e:
                logger.error(f"Error en analizador: {e}")

            if leads or tareas:
                logger.info(f"Ciclo {self.ciclos}: {len(leads)} follow-ups, {len(tareas)} cron")

        except Exception as e:
            logger.error(f"Error en heartbeat ciclo {self.ciclos}: {e}")

        self._programar_siguiente()


# ============================================================
# MENSAJES DE FOLLOW-UP
# ============================================================

FOLLOWUP_TEMPLATES = [
    # Follow-up #1 (30 min)
    (
        "Hola, solo queria asegurarme de que recibiste la informacion. "
        "Te quedo alguna duda sobre las opciones que vimos? Aqui estoy."
    ),
    # Follow-up #2 (2 horas)
    (
        "Hola de nuevo. Se que estas evaluando tus opciones y queria recordarte "
        "que puedo conectarte con un asesor hoy mismo, sin compromiso. "
        "Quieres que te llamen?"
    ),
    # Follow-up #3 (24 horas)
    (
        "Hola, {agent_name} por aqui. Solo queria saber si tienes alguna pregunta "
        "sobre los planes que revisamos. Si quieres hablar con un asesor, "
        "dime y lo agendo para ti."
    ),
    # Follow-up #4 (48 horas)
    (
        "Hola, este es mi ultimo mensaje por ahora. Si en algun momento "
        "necesitas ayuda con tu cobertura de salud, aqui estare. "
        "Que tengas un excelente dia!"
    ),
]


def generar_mensaje_followup(followup_num: int, agent_name: str = "Sara") -> str:
    idx = min(followup_num - 1, len(FOLLOWUP_TEMPLATES) - 1)
    return FOLLOWUP_TEMPLATES[idx].format(agent_name=agent_name)
