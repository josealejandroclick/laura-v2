"""
LAURA CORE -- Cerebro del agente de reclutamiento EQUITY

Tools registrados:
- calificar_lead       → clasifica por tipo de licencia
- registrar_equity     → guarda en Supabase + GHL Equity
- notificar_equipo     → avisa a Telegram (2 grupos) + email
- consultar_conocimiento → base de conocimiento EQUITY
- agendar_tarea        → follow-up automático (heartbeat)
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
    """Devuelve fecha, hora y fechas precalculadas."""
    try:
        from datetime import timedelta
        ahora = datetime.now(_TZ_ET) if _TZ_ET else datetime.now()
        dias = ["lunes","martes","miercoles","jueves","viernes","sabado","domingo"]
        dia_semana = dias[ahora.weekday()]
        hora_str = ahora.strftime("%I:%M %p")
        fecha_str = ahora.strftime("%d/%m/%Y")
        es_horario_contacto = ahora.weekday() < 5 and 9 <= ahora.hour < 20

        manana = ahora + timedelta(days=1)
        dia_manana = dias[manana.weekday()]
        fecha_manana = manana.strftime("%d/%m/%Y")

        proximo_habil = ahora + timedelta(days=1)
        while proximo_habil.weekday() >= 5:
            proximo_habil += timedelta(days=1)
        dia_proximo_habil = dias[proximo_habil.weekday()]
        fecha_proximo_habil = proximo_habil.strftime("%d/%m/%Y")

        # Overview ocurre martes y jueves a las 6:30pm ET
        dias_overview = []
        for i in range(7):
            d = ahora + timedelta(days=i+1)
            if d.weekday() in (1, 3):  # martes=1, jueves=3
                dias_overview.append(f"{dias[d.weekday()]} {d.strftime('%d/%m/%Y')} a las 6:30 PM ET")
                if len(dias_overview) == 2:
                    break
        proximos_overview = " | ".join(dias_overview)

        horario = "HORARIO DE CONTACTO ACTIVO" if es_horario_contacto else "FUERA DE HORARIO — contactar próximo día hábil"

        return (
            f"[CONTEXTO DEL SISTEMA]\n"
            f"Hoy es: {dia_semana} {fecha_str}\n"
            f"Hora actual (ET): {hora_str}\n"
            f"Estado: {horario}\n"
            f"Mañana es: {dia_manana} {fecha_manana}\n"
            f"Próximo día hábil: {dia_proximo_habil} {fecha_proximo_habil}\n"
            f"Próximos Overview (martes/jueves 6:30pm ET): {proximos_overview}\n"
            f"IMPORTANTE: Usar SIEMPRE estas fechas exactas. NUNCA calcules fechas por tu cuenta.\n"
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
from tools import calificar_lead, registrar_equity, notificar_equipo, consultar_conocimiento
from heartbeat import (
    registrar_actividad,
    TOOL_SCHEMA as AGENDAR_SCHEMA,
    ejecutar_agendar
)

# ============================================================
# HERRAMIENTAS
# ============================================================

TOOL_SCHEMAS = [
    calificar_lead.TOOL_SCHEMA,
    registrar_equity.TOOL_SCHEMA,
    notificar_equipo.TOOL_SCHEMA,
    consultar_conocimiento.TOOL_SCHEMA,
    AGENDAR_SCHEMA,
]

TOOL_HANDLERS = {
    "calificar_lead":        calificar_lead.ejecutar,
    "registrar_equity":      registrar_equity.ejecutar,
    "notificar_equipo":      notificar_equipo.ejecutar,
    "consultar_conocimiento": consultar_conocimiento.ejecutar,
    "agendar_tarea":         ejecutar_agendar,
}

# Tools que reciben session_id como parámetro extra
TOOLS_CON_SESSION = {"agendar_tarea"}

# ============================================================
# AGENTE
# ============================================================

class LauraAgente:
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
                "souls", "laura_equity.md"
            )
            try:
                with open(fallback, "r", encoding="utf-8") as f:
                    return f.read()
            except FileNotFoundError:
                return "Eres Laura, asistente de reclutamiento del Programa EQUITY de MKAddesh."

    def procesar(self, session_id: str, user_input: str, extra_context: dict = None) -> str:
        """
        Procesa un mensaje y devuelve la respuesta.
        extra_context: dict opcional con datos adicionales
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
                            if bloque.name in TOOLS_CON_SESSION:
                                kwargs["session_id"] = session_id
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

def crear_agente(soul_path: str = None) -> LauraAgente:
    global _agente_default
    if _agente_default is None:
        _agente_default = LauraAgente(soul_path=soul_path)
    return _agente_default

def procesar_mensaje(agente: LauraAgente, session_id: str, texto: str) -> str:
    return agente.procesar(session_id, texto)
