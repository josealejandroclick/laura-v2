"""
SARA -- Reportes automaticos
Diario: lunes a sabado 8am ET
Semanal: domingos 7pm ET
Mensual: dia 1 de cada mes 8am ET

Destinatarios Telegram: 483808943, 1223584014
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

logger = logging.getLogger("sara_reports")

# ── Configuracion ────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("NOTIFY_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
REPORT_CHAT_IDS = ["483808943", "1223584014"]

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

STAGES_DESCARTADOS = {
    "no elegible", "no show", "reprogramar", "no interesado",
    "caido", "no completo", "reciclar", "no show / reprogramar",
    "caido / no completo"
}

TAGS_CERRADOS = {"p_cliente_cerrado", "cliente_dental"}

# Control para evitar doble envio
_ultimo_reporte = {"diario": None, "semanal": None, "mensual": None}


# ── Helpers ──────────────────────────────────────────────────

def _ahora_et() -> datetime:
    if TZ_ET:
        return datetime.now(TZ_ET)
    return datetime.utcnow()


def _enviar_telegram(mensaje: str):
    if not TELEGRAM_BOT_TOKEN:
        logger.error("[REPORT] TELEGRAM_BOT_TOKEN no configurado")
        return
    for chat_id in REPORT_CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            r = httpx.post(url, json={
                "chat_id": chat_id,
                "text": mensaje,
                "parse_mode": "Markdown"
            }, timeout=10)
            if r.status_code == 200:
                logger.info(f"[REPORT] Reporte enviado a {chat_id}")
            else:
                logger.error(f"[REPORT] Error Telegram {chat_id} {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"[REPORT] Error enviando a {chat_id}: {e}")


def _obtener_sesiones_supabase(desde: datetime, hasta: datetime) -> list:
    """Obtiene sesiones de WhatsApp usando columna actualizado_en."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        # Formatear sin microsegundos para compatibilidad
        desde_iso = desde.strftime("%Y-%m-%dT%H:%M:%S")
        hasta_iso = hasta.strftime("%Y-%m-%dT%H:%M:%S")

        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/sara_v2_sesiones",
            headers=headers,
            params={
                "select": "session_id,mensajes,temperatura,creado_en,actualizado_en",
                "session_id": "like.whatsapp_*",
                "actualizado_en": f"gte.{desde_iso}",
                "order": "actualizado_en.desc"
            },
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            logger.info(f"[REPORT] Supabase devolvio {len(data)} sesiones desde {desde_iso}")
            return data
        else:
            logger.error(f"[REPORT] Error Supabase {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[REPORT] Error Supabase: {e}")
    return []


def _obtener_contactos_ghl(desde: datetime) -> list:
    if not GHL_API_KEY or not GHL_LOCATION_ID:
        return []
    try:
        headers = {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Version": "2021-04-15"
        }
        r = httpx.get(
            f"{GHL_BASE_URL}/contacts/search",
            headers=headers,
            params={
                "locationId": GHL_LOCATION_ID,
                "query": "lead_whatsapp",
                "limit": 100
            },
            timeout=15
        )
        if r.status_code == 200:
            contactos = r.json().get("contacts", [])
            logger.info(f"[REPORT] GHL devolvio {len(contactos)} contactos")
            return contactos
    except Exception as e:
        logger.error(f"[REPORT] Error GHL contactos: {e}")
    return []


def _clasificar_contacto(contacto: dict) -> str:
    tags = set(t.lower() for t in contacto.get("tags", []))
    stage_nombre = (contacto.get("stage", {}) or {}).get("name", "").lower()

    if tags & TAGS_CERRADOS:
        return "cerrado"

    for s in STAGES_DESCARTADOS:
        if s in stage_nombre:
            return "descartado"

    if "cita agendada" in stage_nombre:
        return "agendado"

    return "en_seguimiento"


def _analisis_semantico_claude(sesiones: list, periodo: str) -> str:
    if not ANTHROPIC_API_KEY or not sesiones:
        return "No hay suficientes datos para el analisis semantico."

    muestra = []
    for sesion in sesiones[:20]:
        mensajes = sesion.get("mensajes", [])
        if isinstance(mensajes, str):
            try:
                mensajes = json.loads(mensajes)
            except Exception:
                continue
        textos = []
        for m in mensajes:
            if isinstance(m, dict) and m.get("role") == "user":
                contenido = m.get("content", "")
                if isinstance(contenido, str) and len(contenido) < 500:
                    textos.append(contenido)
        if textos:
            muestra.append(" | ".join(textos[:5]))

    if not muestra:
        return "No hay mensajes de clientes para analizar."

    resumen_conversaciones = "\n".join([f"- {t}" for t in muestra])

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
                "max_tokens": 400,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"Analiza estos mensajes de leads de seguros de salud del {periodo}. "
                        f"Dame un parrafo corto (maximo 5 oraciones) sobre: "
                        f"1) Principales preocupaciones o preguntas, "
                        f"2) Nivel general de interes, "
                        f"3) Objeciones mas comunes. "
                        f"Responde en espanol, tono ejecutivo y conciso.\n\n"
                        f"Mensajes:\n{resumen_conversaciones}"
                    )
                }]
            },
            timeout=30
        )
        if r.status_code == 200:
            content = r.json().get("content", [])
            for bloque in content:
                if bloque.get("type") == "text":
                    return bloque.get("text", "").strip()
    except Exception as e:
        logger.error(f"[REPORT] Error analisis semantico: {e}")

    return "No se pudo generar el analisis semantico."


# ── Calculo de metricas ───────────────────────────────────────

def _calcular_metricas(sesiones: list, contactos: list) -> dict:
    total_sesiones = len(sesiones)

    # Temperatura desde campo directo en Supabase
    calientes = tibios = frios = 0
    for s in sesiones:
        temp = (s.get("temperatura") or "").upper()
        if temp == "CALIENTE":
            calientes += 1
        elif temp == "TIBIO":
            tibios += 1
        elif temp == "FRIO":
            frios += 1

    agendados = cerrados = descartados = en_seguimiento = 0
    for c in contactos:
        clasificacion = _clasificar_contacto(c)
        if clasificacion == "agendado":
            agendados += 1
        elif clasificacion == "cerrado":
            cerrados += 1
        elif clasificacion == "descartado":
            descartados += 1
        else:
            en_seguimiento += 1

    return {
        "total": total_sesiones,
        "calientes": calientes,
        "tibios": tibios,
        "frios": frios,
        "agendados": agendados,
        "cerrados": cerrados,
        "descartados": descartados,
        "en_seguimiento": en_seguimiento
    }


# ── Reportes ─────────────────────────────────────────────────

def ejecutar_diario():
    ahora = _ahora_et()
    clave = ahora.strftime("%Y-%m-%d")
    if _ultimo_reporte["diario"] == clave:
        logger.info("[REPORT] Reporte diario ya enviado hoy, omitiendo")
        return
    _ultimo_reporte["diario"] = clave

    ayer_inicio = (ahora - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ayer_fin = ayer_inicio.replace(hour=23, minute=59, second=59)
    fecha_str = ayer_inicio.strftime("%d/%m/%Y")

    logger.info(f"[REPORT] Generando reporte diario de {fecha_str}")

    sesiones = _obtener_sesiones_supabase(ayer_inicio, ayer_fin)
    contactos = _obtener_contactos_ghl(ayer_inicio)
    metricas = _calcular_metricas(sesiones, contactos)

    mensaje = (
        f"📊 *REPORTE DIARIO SARA — {fecha_str}*\n\n"
        f"👥 Leads nuevos: *{metricas['total']}*\n"
        f"🔥 Calientes: *{metricas['calientes']}* | "
        f"🌡 Tibios: *{metricas['tibios']}* | "
        f"❄️ Frios: *{metricas['frios']}*\n\n"
        f"📅 Citas agendadas: *{metricas['agendados']}*\n"
        f"👀 En seguimiento: *{metricas['en_seguimiento']}*\n"
        f"❌ Descartados: *{metricas['descartados']}*\n"
        f"✅ Cerrados: *{metricas['cerrados']}*\n\n"
        f"📌 Total atendidos: *{metricas['total']}*"
    )

    _enviar_telegram(mensaje)


def ejecutar_semanal():
    ahora = _ahora_et()
    semana_num = ahora.isocalendar()[1]
    clave = f"{ahora.year}-W{semana_num}"
    if _ultimo_reporte["semanal"] == clave:
        logger.info("[REPORT] Reporte semanal ya enviado esta semana, omitiendo")
        return
    _ultimo_reporte["semanal"] = clave

    inicio_semana = (ahora - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    periodo_str = f"{inicio_semana.strftime('%d/%m')} al {ahora.strftime('%d/%m/%Y')}"

    logger.info(f"[REPORT] Generando reporte semanal semana {semana_num}")

    sesiones = _obtener_sesiones_supabase(inicio_semana, ahora)
    contactos = _obtener_contactos_ghl(inicio_semana)
    metricas = _calcular_metricas(sesiones, contactos)
    semantica = _analisis_semantico_claude(sesiones, f"la semana {semana_num}")

    mensaje = (
        f"📈 *REPORTE SEMANAL SARA — Semana {semana_num}*\n"
        f"_{periodo_str}_\n\n"
        f"👥 Total leads: *{metricas['total']}*\n"
        f"🔥 Calientes: *{metricas['calientes']}* | "
        f"🌡 Tibios: *{metricas['tibios']}* | "
        f"❄️ Frios: *{metricas['frios']}*\n\n"
        f"📅 Agendados: *{metricas['agendados']}* | "
        f"✅ Cerrados: *{metricas['cerrados']}*\n"
        f"👀 En seguimiento: *{metricas['en_seguimiento']}*\n"
        f"❌ Descartados: *{metricas['descartados']}*\n\n"
        f"🧠 *ANALISIS SEMANTICO:*\n{semantica}"
    )

    _enviar_telegram(mensaje)


def ejecutar_mensual():
    ahora = _ahora_et()
    mes_anterior = (ahora.replace(day=1) - timedelta(days=1))
    clave = mes_anterior.strftime("%Y-%m")
    if _ultimo_reporte["mensual"] == clave:
        logger.info("[REPORT] Reporte mensual ya enviado este mes, omitiendo")
        return
    _ultimo_reporte["mensual"] = clave

    inicio_mes = mes_anterior.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    mes_str = mes_anterior.strftime("%B %Y")

    logger.info(f"[REPORT] Generando reporte mensual de {mes_str}")

    sesiones = _obtener_sesiones_supabase(inicio_mes, mes_anterior)
    contactos = _obtener_contactos_ghl(inicio_mes)
    metricas = _calcular_metricas(sesiones, contactos)
    semantica = _analisis_semantico_claude(sesiones, mes_str)

    mensaje = (
        f"📅 *REPORTE MENSUAL SARA — {mes_str.upper()}*\n\n"
        f"👥 Total leads: *{metricas['total']}*\n"
        f"🔥 Calientes: *{metricas['calientes']}* | "
        f"🌡 Tibios: *{metricas['tibios']}* | "
        f"❄️ Frios: *{metricas['frios']}*\n\n"
        f"📅 Agendados: *{metricas['agendados']}* | "
        f"✅ Cerrados: *{metricas['cerrados']}*\n"
        f"👀 En seguimiento: *{metricas['en_seguimiento']}*\n"
        f"❌ Descartados: *{metricas['descartados']}*\n\n"
        f"🧠 *ANALISIS SEMANTICO DEL MES:*\n{semantica}"
    )

    _enviar_telegram(mensaje)


# ── Verificacion de horario ───────────────────────────────────

def verificar_y_ejecutar():
    ahora = _ahora_et()
    hora = ahora.hour
    minuto = ahora.minute
    dia_semana = ahora.weekday()  # 0=lunes, 6=domingo
    dia_mes = ahora.day

    # Ventana de 5 minutos para evitar doble envio
    # Diario: 8am ET, lunes a sabado (0-5)
    if hora == 8 and minuto < 5 and dia_semana < 6:
        ejecutar_diario()

    # Semanal: domingo (6) a las 7pm ET
    if hora == 19 and minuto < 5 and dia_semana == 6:
        ejecutar_semanal()

    # Mensual: dia 1 a las 8am ET
    if dia_mes == 1 and hora == 8 and minuto < 5:
        ejecutar_mensual()
