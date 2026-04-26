"""
LAURA -- Heartbeat + Cron (Agente Proactivo)
Bot de reclutamiento EQUITY - MKAddesh

Follow-up con intervalos variables:
#1 -> 30 minutos
#2 -> 2 horas
#3 -> 24 horas
#4 -> 48 horas

Follow-ups de WhatsApp se envían via GHL automáticamente.
Antes de cada follow-up verifica tags del contacto:
- humano_activo       -> detener seguimiento
- contratado          -> detener seguimiento
- equity_con_lic      -> no detener, pero no vender — ya calificado
- estudiante_lic      -> no detener, está en proceso de escuela
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

logger = logging.getLogger("laura_heartbeat")

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
TAGS_DETENER = {"humano_activo", "contratado"}

# Tags que indican que el candidato está en proceso activo (no detener pero no presionar)
TAGS_EN_PROCESO = {"equity_con_lic", "estudiante_lic"}

# Control para recordatorios ya enviados
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
# GHL — VERIFICACION DE TAGS
# ============================================================

def _extraer_telefono_whatsapp(session_id: str) -> str:
    if session_id.startswith("whatsapp_"):
        return session_id[len("whatsapp_"):]
    return ""


def _buscar_datos_contacto_ghl(telefono: str) -> dict:
    resultado = {
        "contacto_id": "",
        "conversation_id": "",
        "tags": [],
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
        r = httpx.get(
            f"{GHL_BASE_URL}/contacts/",
            headers=headers,
            params={"locationId": GHL_LOCATION_ID, "query": telefono_query},
            timeout=8
        )
        if r.status_code == 200:
            contactos = r.json().get("contacts", [])
            if contactos:
                contacto = contactos[0]
                contacto_id = contacto.get("id", "")
                resultado["contacto_id"] = contacto_id
                resultado["tags"] = contacto.get("tags", [])

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

    except Exception as e:
        logger.warning(f"[FOLLOWUP-GHL] Error buscando datos de {telefono_query}: {e}")

    return resultado


def _enviar_mensaje_ghl(conversation_id: str, contacto_id: str, mensaje: str) -> bool:
    """Envía un mensaje WhatsApp via GHL."""
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
        logger.error(f"[FOLLOWUP-GHL] Excepción enviando mensaje: {e}")
        return False


def _procesar_followup_whatsapp(session_id: str, followup_num: int) -> bool:
    """
    Procesa un follow-up de WhatsApp para candidatos EQUITY.
    1. Verifica tags del contacto
    2. Si tiene tag de detener → desactiva sesión
    3. Si está en proceso activo → no presionar, solo recordatorio suave
    4. Si no tiene tags especiales → envía follow-up de reclutamiento normal
    """
    telefono = _extraer_telefono_whatsapp(session_id)
    if not telefono:
        return False

    datos = _buscar_datos_contacto_ghl(telefono)
    contacto_id = datos.get("contacto_id", "")
    conversation_id = datos.get("conversation_id", "")
    tags = set(datos.get("tags", []))

    # Tags que detienen el seguimiento
    if tags & TAGS_DETENER:
        logger.info(f"[FOLLOWUP] {session_id} tiene tag de detener ({tags & TAGS_DETENER}) — desactivando")
        desactivar_sesion(session_id)
        return True

    if not contacto_id or not conversation_id:
        logger.warning(f"[FOLLOWUP-GHL] No se encontró contacto/conversación para {telefono}")
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
        "o cualquier acción que deba ejecutarse en una fecha/hora específica."
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
                "description": "Qué hacer cuando llegue la hora"
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
        logger.info(f"Heartbeat detenido después de {self.ciclos} ciclos.")

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

            # Reportes automáticos
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
# MENSAJES DE FOLLOW-UP — RECLUTAMIENTO EQUITY
# ============================================================

FOLLOWUP_TEMPLATES = [
    # Follow-up #1 (30 min)
    (
        "Hola, solo quería asegurarme de que te llegó la información del Programa EQUITY. "
        "¿Te quedó alguna duda sobre lo que hablamos? Aquí estoy."
    ),

    # Follow-up #2 (2 horas)
    (
        "Hola de nuevo. Sé que estás evaluando tus opciones. "
        "Si quieres, puedo hacer que alguien del equipo de MKAddesh te llame "
        "para resolver tus dudas de forma personalizada, sin compromiso. "
        "¿Te parece bien?"
    ),

    # Follow-up #3 (24 horas)
    (
        "Hola, {agent_name} por aquí. Quería saber si tienes alguna pregunta "
        "sobre el Programa EQUITY. "
        "Tengo disponible conectarte con un asesor del equipo hoy mismo — "
        "solo dime si te interesa y lo coordino."
    ),

    # Follow-up #4 (48 horas)
    (
        "Hola, este es mi último mensaje por ahora. "
        "Si en algún momento quieres que alguien del equipo te explique el Programa EQUITY "
        "en detalle, escríbeme y lo coordino de inmediato. "
        "¡Que tengas un excelente día!"
    ),
]


def generar_mensaje_followup(followup_num: int, agent_name: str = "Laura") -> str:
    idx = min(followup_num - 1, len(FOLLOWUP_TEMPLATES) - 1)
    return FOLLOWUP_TEMPLATES[idx].format(agent_name=agent_name)
