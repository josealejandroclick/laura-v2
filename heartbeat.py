"""
SAM -- Heartbeat + Cron (Agente Proactivo)

Follow-up con intervalos variables:
  #1 -> 30 minutos
  #2 -> 2 horas
  #3 -> 24 horas
  #4 -> 48 horas
"""

import json
import os
import time
import threading
import logging
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
    """
    Marca que hubo actividad en una sesion.
    Resetea el contador de follow-ups cuando el cliente responde.
    """
    tracker = _cargar_tracker()
    tracker[session_id] = {
        "ultimo_mensaje": time.time(),
        "followups_enviados": 0,
        "activo": True
    }
    _guardar_tracker(tracker)


def obtener_leads_para_followup() -> list:
    """
    Devuelve lista de (session_id, followup_num) donde followup_num
    indica cual follow-up toca enviar segun los intervalos configurados.
    Solo incluye session_ids numericos validos de Telegram.
    """
    tracker = _cargar_tracker()
    ahora = time.time()
    leads = []

    for session_id, data in tracker.items():
        if not str(session_id).lstrip("-").isdigit():
            continue
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
                leads.append((session_id, enviados + 1))

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
            for session_id, followup_num in leads:
                logger.info(f"Follow-up #{followup_num} para {session_id}")
                if self.on_followup:
                    try:
                        self.on_followup(session_id, followup_num)
                        marcar_followup_enviado(session_id)
                    except Exception as e:
                        logger.error(f"Error en follow-up {session_id}: {e}")

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
        "Hola solo queria asegurarme de que recibiste la informacion. "
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
