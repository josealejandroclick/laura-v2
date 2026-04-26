"""
LAURA -- Agente Analizador Semanal de Reclutamiento EQUITY
Ejecuta domingos a las 7pm ET (Sara es 6pm)
Analiza conversaciones de la semana y genera recomendaciones
para mejorar el soul y knowledge de Laura.

Destinatarios: 483808943, 1223584014
"""

import os
import json
import logging
import httpx
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    TZ_ET = ZoneInfo("America/New_York")
except ImportError:
    TZ_ET = None

logger = logging.getLogger("laura_analyzer")

# ── Configuración ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("NOTIFY_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
REPORT_CHAT_IDS = ["483808943", "1223584014"]

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Control para evitar doble envío
_ultimo_analisis = {"semanal": None}

# ── Resumen del soul de Laura como referencia para el análisis ─
SOUL_RESUMEN = """
Laura es asistente de reclutamiento del Programa EQUITY de MKAddesh Holding.
Su objetivo es calificar candidatos para unirse como agentes de seguros.

Flujo: Captura básica (nombre, email, teléfono, estado, fuente) →
Clasificación por licencia → Derivar a camino correcto

Caminos:
- Licencia 215/220/240 → equity_con_lic → Formulario de contratación
- Sin licencia → equity_sin_lic → Escuela de Licenciamiento
- Licencia 214 → lic_214 → Escuela para agregar licencia de salud
- Otra licencia → escalar a asesor humano

Reglas críticas:
- No dar montos de comisiones
- No dar fechas que no estén en el contexto del sistema
- No prometer resultados económicos específicos
- Una pregunta por mensaje
- Nunca frases robóticas como "Excelente", "Por supuesto", "Claro que sí"

Gerentes de línea: Jimmy Arenas, Isidro González, Jamie Varona, Andy Salandy, Daniel Pulido
Overview: martes y jueves 6:30pm ET
"""


# ── Helpers ───────────────────────────────────────────────────

def _ahora_et() -> datetime:
    if TZ_ET:
        return datetime.now(TZ_ET)
    return datetime.utcnow()


def _enviar_telegram(mensaje: str):
    if not TELEGRAM_BOT_TOKEN:
        logger.error("[ANALYZER] TELEGRAM_BOT_TOKEN no configurado")
        return
    for chat_id in REPORT_CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            r = httpx.post(url, json={
                "chat_id": chat_id,
                "text": mensaje,
                "parse_mode": "Markdown"
            }, timeout=15)
            if r.status_code == 200:
                logger.info(f"[ANALYZER] Enviado a {chat_id}")
            else:
                logger.error(f"[ANALYZER] Error {chat_id} {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"[ANALYZER] Error enviando a {chat_id}: {e}")


def _obtener_conversaciones_semana() -> list:
    """Obtiene conversaciones de WhatsApp de Laura de los últimos 7 días."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        ahora = _ahora_et()
        hace_7_dias = (ahora - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/conversaciones_laura",
            headers=headers,
            params={
                "select": "session_id,mensajes,etapa,datos_capturados,updated_at",
                "session_id": "like.whatsapp_*",
                "updated_at": f"gte.{hace_7_dias}",
                "order": "updated_at.desc"
            },
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            logger.info(f"[ANALYZER] {len(data)} conversaciones obtenidas para análisis")
            return data
        else:
            logger.error(f"[ANALYZER] Error Supabase {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[ANALYZER] Error Supabase: {e}")
    return []


def _obtener_leads_semana() -> list:
    """Obtiene leads registrados esta semana para métricas."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        ahora = _ahora_et()
        hace_7_dias = (ahora - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/leads_equity",
            headers=headers,
            params={
                "select": "tag_principal,etapa_actual,origen,tiene_licencia",
                "created_at": f"gte.{hace_7_dias}",
                "order": "created_at.desc"
            },
            timeout=15
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"[ANALYZER] Error obteniendo leads: {e}")
    return []


def _extraer_conversaciones(sesiones: list) -> list:
    """Extrae el texto de las conversaciones en formato legible."""
    conversaciones = []

    for sesion in sesiones:
        session_id = sesion.get("session_id", "")
        etapa = sesion.get("etapa", "")
        datos = sesion.get("datos_capturados", {}) or {}
        mensajes = sesion.get("mensajes", [])

        if isinstance(mensajes, str):
            try:
                mensajes = json.loads(mensajes)
            except Exception:
                continue

        lineas = []
        for m in mensajes:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "")
            contenido = m.get("content", "")

            if role == "user" and isinstance(contenido, str):
                lineas.append(f"CANDIDATO: {contenido[:300]}")
            elif role == "assistant" and isinstance(contenido, str):
                lineas.append(f"LAURA: {contenido[:300]}")
            elif role == "assistant" and isinstance(contenido, list):
                for bloque in contenido:
                    if isinstance(bloque, dict) and bloque.get("type") == "text":
                        texto = bloque.get("text", "")
                        if texto:
                            lineas.append(f"LAURA: {texto[:300]}")

        calificado = etapa in ("formulario_contratacion", "escuela_licenciamiento", "calificado")

        if lineas:
            conversaciones.append({
                "session_id": session_id,
                "calificado": calificado,
                "etapa": etapa,
                "texto": "\n".join(lineas[:30])
            })

    return conversaciones


def _analizar_con_claude(conversaciones: list, leads: list, semana_num: int) -> str:
    """Envía las conversaciones a Claude para análisis profundo."""
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY no configurado."

    if not conversaciones:
        return "No hay conversaciones suficientes para analizar esta semana."

    exitosas = [c for c in conversaciones if c.get("calificado")]
    incompletas = [c for c in conversaciones if not c.get("calificado")]

    muestra_exitosas = exitosas[:5]
    muestra_incompletas = incompletas[:10]

    texto_exitosas = "\n\n---\n".join([
        f"[CALIFICADO - etapa: {c['etapa']}]\n{c['texto']}"
        for c in muestra_exitosas
    ]) or "Ninguna esta semana."

    texto_incompletas = "\n\n---\n".join([
        f"[NO CALIFICADO - etapa: {c['etapa']}]\n{c['texto']}"
        for c in muestra_incompletas
    ]) or "Ninguna esta semana."

    total_leads = len(leads)
    con_lic = sum(1 for l in leads if l.get("tag_principal") == "equity_con_lic")
    sin_lic = sum(1 for l in leads if l.get("tag_principal") in ("equity_sin_lic", "lic_214"))
    tasa = round((con_lic / total_leads * 100), 1) if total_leads > 0 else 0

    prompt = f"""Eres un coach de reclutamiento especializado en agencias de seguros para el mercado hispano en EE.UU.

Tienes acceso a las conversaciones de Laura, una IA de reclutamiento del Programa EQUITY de MKAddesh, de la semana {semana_num}.
Tu trabajo es analizar estas conversaciones y dar recomendaciones CONCRETAS y ESPECÍFICAS
para mejorar el rendimiento de Laura en la calificación de candidatos.

CONTEXTO DE LAURA:
{SOUL_RESUMEN}

ESTADÍSTICAS DE LA SEMANA:
- Total leads: {total_leads}
- Con licencia calificada: {con_lic} ({tasa}%)
- Sin licencia / lic. 214: {sin_lic}
- Conversaciones completadas: {len(exitosas)}
- Conversaciones incompletas: {len(incompletas)}

CONVERSACIONES COMPLETADAS (candidatos calificados):
{texto_exitosas}

CONVERSACIONES INCOMPLETAS (candidatos que no llegaron al final):
{texto_incompletas}

Analiza y responde EXACTAMENTE con estas 4 secciones:

**SECCIÓN 1 — PREGUNTAS SIN RESPUESTA O MAL RESPONDIDAS**
Lista hasta 3 preguntas o situaciones que Laura no manejó bien esta semana.
Para cada una escribe:
- La situación del candidato
- Cómo respondió Laura (el problema)
- Texto exacto sugerido para agregar al knowledge base

**SECCIÓN 2 — PUNTO DE ABANDONO PRINCIPAL**
Identifica EN QUÉ PASO del flujo se pierden más candidatos.
Cita ejemplos específicos.
Sugiere el cambio exacto para ese paso.

**SECCIÓN 3 — OBJECIONES O DUDAS NUEVAS DE LA SEMANA**
Lista hasta 3 objeciones o preguntas frecuentes que aparecieron.
Para cada una escribe la respuesta exacta que Laura debería dar.

**SECCIÓN 4 — UN CAMBIO DE ALTO IMPACTO**
Cuál es EL cambio más importante esta semana.
Específico — qué frase cambiar, qué agregar, qué eliminar.

Responde en español. Tono directo, ejecutivo. Todo debe ser accionable."""

    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60
        )
        if r.status_code == 200:
            content = r.json().get("content", [])
            for bloque in content:
                if bloque.get("type") == "text":
                    return bloque.get("text", "").strip()
        else:
            logger.error(f"[ANALYZER] Error Claude {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[ANALYZER] Error Claude: {e}")

    return "No se pudo generar el análisis esta semana."


def ejecutar_analisis_semanal():
    """Ejecuta el análisis semanal — domingos a las 7pm ET."""
    ahora = _ahora_et()
    semana_num = ahora.isocalendar()[1]
    clave = f"{ahora.year}-W{semana_num}"

    if _ultimo_analisis["semanal"] == clave:
        logger.info("[ANALYZER] Análisis semanal ya enviado, omitiendo")
        return
    _ultimo_analisis["semanal"] = clave

    logger.info(f"[ANALYZER] Iniciando análisis semanal Laura semana {semana_num}")

    conversaciones_raw = _obtener_conversaciones_semana()
    leads = _obtener_leads_semana()

    if not conversaciones_raw and not leads:
        _enviar_telegram(
            f"🔍 *ANÁLISIS SEMANAL LAURA EQUITY — Semana {semana_num}*\n\n"
            f"No hay conversaciones ni leads suficientes para analizar esta semana."
        )
        return

    conversaciones = _extraer_conversaciones(conversaciones_raw)
    analisis = _analizar_con_claude(conversaciones, leads, semana_num)

    total = len(leads)
    con_lic = sum(1 for l in leads if l.get("tag_principal") == "equity_con_lic")
    tasa = round((con_lic / total * 100), 1) if total > 0 else 0

    encabezado = (
        f"🔍 *ANÁLISIS SEMANAL LAURA EQUITY — Semana {semana_num}*\n"
        f"_{total} leads | {con_lic} con licencia | {tasa}% calificados_\n\n"
    )

    mensaje_completo = encabezado + analisis

    if len(mensaje_completo) <= 4000:
        _enviar_telegram(mensaje_completo)
    else:
        _enviar_telegram(encabezado + analisis[:3500] + "\n\n_(continúa...)_")
        _enviar_telegram("_(continuación)_\n\n" + analisis[3500:])

    logger.info(f"[ANALYZER] Análisis semanal enviado — semana {semana_num}")


# ── Verificación de horario ────────────────────────────────────

def verificar_y_ejecutar_analisis():
    """
    Verificar si toca ejecutar el análisis.
    Llamado desde heartbeat cada 5 minutos.
    Domingos a las 7pm ET (Sara es 6pm — horario diferente).
    """
    ahora = _ahora_et()
    hora = ahora.hour
    minuto = ahora.minute
    dia_semana = ahora.weekday()  # 6 = domingo

    if hora == 19 and minuto < 5 and dia_semana == 6:
        ejecutar_analisis_semanal()
