"""
SAM CORE -- Cerebro del agente
6 tools registrados:
- verificar_zip
- cotizar_planes
- registrar_lead
- analizar_lead
- consultar_conocimiento
- agendar_tarea
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from anthropic import Anthropic
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    _TZ_ET = ZoneInfo("America/New_York")
except ImportError:
    _TZ_ET = None


def _contexto_tiempo() -> str:
    """Devuelve fecha, hora y si es horario de oficina en zona ET."""
    try:
        ahora = datetime.now(_TZ_ET) if _TZ_ET else datetime.now()
        dia_semana = ["lunes","martes","miercoles","jueves","viernes","sabado","domingo"][ahora.weekday()]
        hora_str = ahora.strftime("%I:%M %p")
        fecha_str = ahora.strftime("%d/%m/%Y")
        es_oficina = ahora.weekday() < 5 and 8 <= ahora.hour < 18
        horario = "HORARIO DE OFICINA -- un asesor puede llamar dentro de la proxima media hora" if es_oficina else "FUERA DE HORARIO -- el asesor contacta al siguiente dia habil"
        return (
            f"[CONTEXTO DEL SISTEMA]\n"
            f"Fecha actual: {dia_semana} {fecha_str}\n"
            f"Hora actual (ET): {hora_str}\n"
            f"Estado: {horario}\n"
            f"Si el cliente pide que lo llamen 'manana', la fecha correcta es {(ahora).strftime('%d/%m/%Y')} sumando 1 dia.\n"
            f"[FIN CONTEXTO]"
        )
    except Exception:
        return ""

from config import (
    ANTHROPIC_API_KEY, MODEL_ID, SOUL_FILE,
    MAX_TOKENS_RESPONSE
)
from sessions import (
    cargar_sesion, guardar_mensaje,
    necesita_compresion, comprimir_sesion
)
from tools import cotizar, registrar_lead, analizar_lead, verificar_zip, consultar_conocimiento
from tools import ghl_registrar_contacto, ghl_agendar_cita, ghl_enviar_mensaje
from heartbeat import (
    registrar_actividad,
    TOOL_SCHEMA as AGENDAR_SCHEMA,
    ejecutar_agendar
)


# ============================================================
# HERRAMIENTAS
# ============================================================

TOOL_SCHEMAS = [
    verificar_zip.TOOL_SCHEMA,
    cotizar.TOOL_SCHEMA,
    registrar_lead.TOOL_SCHEMA,
    analizar_lead.TOOL_SCHEMA,
    consultar_conocimiento.TOOL_SCHEMA,
    AGENDAR_SCHEMA,
    ghl_registrar_contacto.TOOL_SCHEMA,
    ghl_agendar_cita.TOOL_SCHEMA,
    ghl_enviar_mensaje.TOOL_SCHEMA,
]

TOOL_HANDLERS = {
    "verificar_zip":              verificar_zip.ejecutar,
    "cotizar_planes":             cotizar.ejecutar,
    "registrar_lead":             registrar_lead.ejecutar,
    "analizar_lead":              analizar_lead.ejecutar,
    "consultar_conocimiento":     consultar_conocimiento.ejecutar,
    "agendar_tarea":              ejecutar_agendar,
    "ghl_registrar_contacto":     ghl_registrar_contacto.ejecutar,
    "ghl_agendar_cita":           ghl_agendar_cita.ejecutar,
    "ghl_enviar_mensaje":         ghl_enviar_mensaje.ejecutar,
}

# Tools que reciben session_id como parametro extra
TOOLS_CON_SESSION = {"agendar_tarea", "analizar_lead"}

# Tools que reciben contacto_id del extra_context
TOOLS_CON_CONTACTO = {"analizar_lead", "ghl_agendar_cita"}


# ============================================================
# AGENTE
# ============================================================

class SamAgente:

    def __init__(self, api_key: str = None, soul_path: str = None, model: str = None):
        self.api_key = api_key or ANTHROPIC_API_KEY
        self.model = model or MODEL_ID
        self.soul_path = soul_path or SOUL_FILE
        self.client = Anthropic(api_key=self.api_key)
        self.soul = self._cargar_soul()

    def _cargar_soul(self) -> str:
        try:
            with open(self.soul_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            fallback = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "souls", "sara_mkaddesh.md"
            )
            try:
                with open(fallback, "r", encoding="utf-8") as f:
                    return f.read()
            except FileNotFoundError:
                return "Eres Sara, asesora de proteccion financiera de MKAddesh."

    def procesar(self, session_id: str, user_input: str, extra_context: dict = None) -> str:
        """
        Procesa un mensaje y devuelve la respuesta.
        extra_context: dict opcional con datos adicionales a inyectar en tools
                       (ej: {"contacto_id": "abc123"})
        """
        if extra_context is None:
            extra_context = {}

        registrar_actividad(session_id)

        mensajes = cargar_sesion(session_id)
        mensajes.append({"role": "user", "content": user_input})
        guardar_mensaje(session_id, "user", user_input)

        if necesita_compresion(session_id):
            comprimir_sesion(session_id, self.client, self.model, self.soul)
            mensajes = cargar_sesion(session_id)

        # Agent loop
        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS_RESPONSE,
                system=self.soul + "\n\n" + _contexto_tiempo(),
                messages=mensajes,
                tools=TOOL_SCHEMAS,
            )

            mensajes.append({
                "role": "assistant",
                "content": response.content
            })

            # Respuesta final de texto
            if response.stop_reason != "tool_use":
                texto = ""
                for bloque in response.content:
                    if hasattr(bloque, "text"):
                        texto += bloque.text
                guardar_mensaje(session_id, "assistant", texto)
                return texto

            guardar_mensaje(session_id, "assistant", response.content)

            # Ejecutar tools
            resultados = []
            for bloque in response.content:
                if bloque.type == "tool_use":
                    handler = TOOL_HANDLERS.get(bloque.name)
                    try:
                        if handler:
                            kwargs = dict(bloque.input)

                            # Inyectar session_id si el tool lo necesita
                            if bloque.name in TOOLS_CON_SESSION:
                                kwargs["session_id"] = session_id

                            # Inyectar contacto_id del extra_context si el tool lo necesita
                            if bloque.name in TOOLS_CON_CONTACTO:
                                contacto_id = extra_context.get("contacto_id", "")
                                if contacto_id and "contacto_id" not in kwargs:
                                    kwargs["contacto_id"] = contacto_id

                            output = handler(**kwargs)
                        else:
                            output = json.dumps({"error": f"Tool '{bloque.name}' no encontrada"})
                    except Exception as e:
                        output = json.dumps({"error": f"Error ejecutando tool: {str(e)}"})

                    resultados.append({
                        "type": "tool_result",
                        "tool_use_id": bloque.id,
                        "content": output,
                    })

            mensajes.append({"role": "user", "content": resultados})
            guardar_mensaje(session_id, "user", resultados)


# ============================================================
# FUNCIONES DE CONVENIENCIA
# ============================================================

_agente_default = None


def crear_agente(soul_path: str = None) -> SamAgente:
    global _agente_default
    if _agente_default is None:
        _agente_default = SamAgente(soul_path=soul_path)
    return _agente_default


def procesar_mensaje(agente: SamAgente, session_id: str, texto: str) -> str:
    return agente.procesar(session_id, texto)
