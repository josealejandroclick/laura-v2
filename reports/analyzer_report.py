"""
SARA -- Agente Analizador Semanal
Ejecuta domingos a las 6pm ET
Analiza conversaciones de la semana y genera recomendaciones
para mejorar el soul y knowledge de Sara.

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

logger = logging.getLogger("sara_analyzer")

# ── Configuracion ────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("NOTIFY_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
REPORT_CHAT_IDS = ["483808943", "1223584014"]

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Control para evitar doble envio
_ultimo_analisis = {"semanal": None}

# ── Soul y knowledge como referencia para el analisis ────────
SOUL_RESUMEN = """
Sara es asesora de proteccion financiera de MKAddesh. Vende 3 planes:
- Plan Basico: cobertura medica ACA. NO incluye dental.
- Plan Medium: Basico + proteccion por accidentes (paga al cliente, no al hospital)
- Plan Full Cover: Medium + proteccion por hospitalizacion por cualquier causa

Flujo: Detectar contexto -> recopilar ZIP/ingreso/personas -> cotizar -> sembrar dolor -> presentar 3 planes -> manejar objeciones -> cerrar con cita para asesor

Reglas criticas:
- Nunca dar precios
- Nunca mencionar Obamacare, ACA, Washington National
- Nunca prometer cobertura migratoria por escrito
- Nunca prometer que embarazo actual esta cubierto por suplementario
- Una pregunta por mensaje
- Maximo 1-2 lineas por mensaje
- Nunca frases roboticas como "Excelente", "Perfecto", "Claro que si"

Objeciones documentadas: precio, "ya tengo seguro", "es muy caro", "lo tengo que pensar", miedo migratorio, "Obamacare no sirve"
"""


# ── Helpers ──────────────────────────────────────────────────

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


def _obtener_sesiones_semana() -> list:
    """Obtiene todas las sesiones de WhatsApp de los ultimos 7 dias."""
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
            f"{SUPABASE_URL}/rest/v1/sara_v2_sesiones",
            headers=headers,
            params={
                "select": "session_id,mensajes,temperatura,estado,cita_agendada,actualizado_en",
                "session_id": "like.whatsapp_*",
                "actualizado_en": f"gte.{hace_7_dias}",
                "order": "actualizado_en.desc"
            },
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            logger.info(f"[ANALYZER] {len(data)} sesiones obtenidas para analisis")
            return data
        else:
            logger.error(f"[ANALYZER] Error Supabase {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[ANALYZER] Error Supabase: {e}")
    return []


def _extraer_conversaciones(sesiones: list) -> list:
    """
    Extrae el texto de las conversaciones en formato legible.
    Retorna lista de dicts con session_id, cita_agendada y texto_conversacion.
    """
    conversaciones = []

    for sesion in sesiones:
        session_id = sesion.get("session_id", "")
        cita_agendada = sesion.get("cita_agendada", False)
        temperatura = sesion.get("temperatura", "")
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
                lineas.append(f"CLIENTE: {contenido[:300]}")
            elif role == "assistant" and isinstance(contenido, str):
                lineas.append(f"SARA: {contenido[:300]}")
            elif role == "assistant" and isinstance(contenido, list):
                for bloque in contenido:
                    if isinstance(bloque, dict) and bloque.get("type") == "text":
                        texto = bloque.get("text", "")
                        if texto:
                            lineas.append(f"SARA: {texto[:300]}")

        if lineas:
            conversaciones.append({
                "session_id": session_id,
                "cita_agendada": cita_agendada,
                "temperatura": temperatura,
                "texto": "\n".join(lineas[:30])  # max 30 intercambios
            })

    return conversaciones


def _analizar_con_claude(conversaciones: list, semana_num: int) -> str:
    """
    Envia las conversaciones a Claude para analisis profundo como coach de ventas.
    """
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY no configurado."

    if not conversaciones:
        return "No hay conversaciones suficientes para analizar esta semana."

    # Separar conversaciones exitosas vs perdidas
    exitosas = [c for c in conversaciones if c.get("cita_agendada")]
    perdidas = [c for c in conversaciones if not c.get("cita_agendada")]

    # Preparar muestra representativa (max 15 conversaciones)
    muestra_exitosas = exitosas[:5]
    muestra_perdidas = perdidas[:10]

    texto_exitosas = "\n\n---\n".join([
        f"[CITA AGENDADA - {c['temperatura'].upper()}]\n{c['texto']}"
        for c in muestra_exitosas
    ]) or "Ninguna esta semana."

    texto_perdidas = "\n\n---\n".join([
        f"[SIN CITA - {c['temperatura'].upper()}]\n{c['texto']}"
        for c in muestra_perdidas
    ]) or "Ninguna esta semana."

    total = len(conversaciones)
    total_exitosas = len(exitosas)
    tasa = round((total_exitosas / total * 100), 1) if total > 0 else 0

    prompt = f"""Eres un coach de ventas especializado en seguros de salud para el mercado hispano en EE.UU.

Tienes acceso a las conversaciones de Sara, una IA de ventas, de la semana {semana_num}.
Tu trabajo es analizar estas conversaciones y dar recomendaciones CONCRETAS y ESPECIFICAS
para mejorar el rendimiento de Sara.

CONTEXTO DE SARA:
{SOUL_RESUMEN}

ESTADISTICAS DE LA SEMANA:
- Total conversaciones: {total}
- Citas agendadas: {total_exitosas} ({tasa}%)
- Sin cita: {len(perdidas)}

CONVERSACIONES CON CITA AGENDADA (lo que funciona):
{texto_exitosas}

CONVERSACIONES SIN CITA (donde se pierden leads):
{texto_perdidas}

Analiza y responde EXACTAMENTE con estas 4 secciones. Se especifico, usa ejemplos reales de las conversaciones:

**SECCION 1 — PREGUNTAS SIN RESPUESTA O MAL RESPONDIDAS**
Lista hasta 3 preguntas que Sara no supo manejar bien esta semana.
Para cada una, escribe:
- La pregunta del cliente (textual o parafraseada)
- Como respondio Sara (el problema)
- Texto exacto sugerido para agregar al knowledge base

**SECCION 2 — PUNTO DE ABANDONO PRINCIPAL**
Identifica EN QUE PASO del flujo se pierden mas leads.
Cita ejemplos especificos de las conversaciones.
Sugiere el cambio exacto de texto o secuencia para ese paso.

**SECCION 3 — OBJECIONES NUEVAS DE LA SEMANA**
Lista hasta 3 objeciones que aparecieron y no estan bien manejadas.
Para cada una escribe la respuesta exacta que Sara deberia dar.

**SECCION 4 — UN CAMBIO DE ALTO IMPACTO**
Basado en lo que ves, cual es EL cambio mas importante que haria la mayor diferencia esta semana.
Se especifico — que frase cambiar, que agregar, que eliminar.

Responde en espanol. Tono directo, ejecutivo. Sin generalidades — todo debe ser accionable."""

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

    return "No se pudo generar el analisis esta semana."


def ejecutar_analisis_semanal():
    """Ejecuta el analisis semanal — domingos a las 6pm ET."""
    ahora = _ahora_et()
    semana_num = ahora.isocalendar()[1]
    clave = f"{ahora.year}-W{semana_num}"

    if _ultimo_analisis["semanal"] == clave:
        logger.info("[ANALYZER] Analisis semanal ya enviado, omitiendo")
        return
    _ultimo_analisis["semanal"] = clave

    logger.info(f"[ANALYZER] Iniciando analisis semanal semana {semana_num}")

    sesiones = _obtener_sesiones_semana()

    if not sesiones:
        _enviar_telegram(
            f"🔍 *ANALISIS SEMANAL SARA — Semana {semana_num}*\n\n"
            f"No hay conversaciones suficientes para analizar esta semana."
        )
        return

    conversaciones = _extraer_conversaciones(sesiones)
    analisis = _analizar_con_claude(conversaciones, semana_num)

    total = len(sesiones)
    exitosas = sum(1 for s in sesiones if s.get("cita_agendada"))
    tasa = round((exitosas / total * 100), 1) if total > 0 else 0

    encabezado = (
        f"🔍 *ANALISIS SEMANAL SARA — Semana {semana_num}*\n"
        f"_{total} conversaciones | {exitosas} citas | {tasa}% conversion_\n\n"
    )

    mensaje_completo = encabezado + analisis

    # Si el mensaje es muy largo para Telegram (max 4096 chars), dividirlo
    if len(mensaje_completo) <= 4000:
        _enviar_telegram(mensaje_completo)
    else:
        _enviar_telegram(encabezado + analisis[:3500] + "\n\n_(continua...)_")
        _enviar_telegram("_(continuacion)_\n\n" + analisis[3500:])

    logger.info(f"[ANALYZER] Analisis semanal enviado — semana {semana_num}")


# ── Verificacion de horario ───────────────────────────────────

def verificar_y_ejecutar_analisis():
    """
    Verifica si toca ejecutar el analisis.
    Llamado desde heartbeat cada 5 minutos.
    Domingos a las 6pm ET.
    """
    ahora = _ahora_et()
    hora = ahora.hour
    minuto = ahora.minute
    dia_semana = ahora.weekday()  # 6 = domingo

    if hora == 18 and minuto < 5 and dia_semana == 6:
        ejecutar_analisis_semanal()
