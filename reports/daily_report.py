"""
LAURA -- Reportes automáticos de reclutamiento EQUITY
Diario:   lunes a sábado 8am ET
Semanal:  domingos 7pm ET
Mensual:  día 1 de cada mes 8am ET

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

logger = logging.getLogger("laura_reports")

# ── Configuración ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("NOTIFY_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
# Reportes van a los mismos IDs que Sara pero en horario diferente
REPORT_CHAT_IDS = ["483808943", "1223584014"]

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Tags de clasificación EQUITY
TAGS_CON_LICENCIA = {"equity_con_lic"}
TAGS_SIN_LICENCIA = {"equity_sin_lic", "lic_214"}
TAGS_CONTRATADO = {"contratado"}

# Control para evitar doble envío
_ultimo_reporte = {"diario": None, "semanal": None, "mensual": None}


# ── Helpers ───────────────────────────────────────────────────

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


def _obtener_leads_supabase(desde: datetime, hasta: datetime) -> list:
    """Obtiene leads de la tabla leads_equity en el rango de fechas dado."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        desde_iso = desde.strftime("%Y-%m-%dT%H:%M:%S")

        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/leads_equity",
            headers=headers,
            params={
                "select": "id,first_name,last_name,tag_principal,tags,etapa_actual,origen,tiene_licencia,tipo_licencia,created_at",
                "created_at": f"gte.{desde_iso}",
                "order": "created_at.desc"
            },
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            logger.info(f"[REPORT] Supabase devolvió {len(data)} leads desde {desde_iso}")
            return data
        else:
            logger.error(f"[REPORT] Error Supabase {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[REPORT] Error Supabase: {e}")
    return []


def _calcular_metricas(leads: list) -> dict:
    """Calcula métricas de reclutamiento EQUITY."""
    total = len(leads)
    con_licencia = 0
    sin_licencia = 0
    lic_214 = 0
    contratados = 0
    desde_lovable = 0
    desde_whatsapp = 0
    desde_ghl = 0

    for lead in leads:
        tags = set(lead.get("tags") or [])
        tag_principal = lead.get("tag_principal", "")
        origen = lead.get("origen", "")

        if tag_principal == "equity_con_lic" or tags & TAGS_CON_LICENCIA:
            con_licencia += 1
        elif tag_principal == "lic_214":
            lic_214 += 1
        elif tag_principal == "equity_sin_lic" or tags & TAGS_SIN_LICENCIA:
            sin_licencia += 1

        if tags & TAGS_CONTRATADO:
            contratados += 1

        if origen == "lovable":
            desde_lovable += 1
        elif origen == "whatsapp_laura":
            desde_whatsapp += 1
        elif origen == "ghl":
            desde_ghl += 1

    return {
        "total": total,
        "con_licencia": con_licencia,
        "sin_licencia": sin_licencia,
        "lic_214": lic_214,
        "contratados": contratados,
        "desde_lovable": desde_lovable,
        "desde_whatsapp": desde_whatsapp,
        "desde_ghl": desde_ghl,
    }


def _analisis_semantico_claude(leads: list, periodo: str) -> str:
    """Genera análisis con Claude sobre los leads del periodo."""
    if not ANTHROPIC_API_KEY or not leads:
        return "No hay suficientes datos para el análisis."

    resumen = []
    for lead in leads[:20]:
        nombre = f"{lead.get('first_name','')} {lead.get('last_name','')}".strip()
        tag = lead.get("tag_principal", "sin_tag")
        licencia = lead.get("tipo_licencia", "ninguna")
        origen = lead.get("origen", "desconocido")
        resumen.append(f"- {nombre}: tag={tag}, licencia={licencia}, origen={origen}")

    texto_leads = "\n".join(resumen)

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
                        f"Analiza estos leads de reclutamiento del Programa EQUITY de MKAddesh del {periodo}. "
                        f"Dame un párrafo corto (máximo 5 oraciones) sobre: "
                        f"1) Perfil predominante de candidatos, "
                        f"2) Proporción con vs sin licencia, "
                        f"3) Fuente de leads más activa, "
                        f"4) Una recomendación de acción. "
                        f"Responde en español, tono ejecutivo y conciso.\n\n"
                        f"Leads:\n{texto_leads}"
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
        logger.error(f"[REPORT] Error análisis semántico: {e}")

    return "No se pudo generar el análisis semántico."


# ── Reportes ──────────────────────────────────────────────────

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

    logger.info(f"[REPORT] Generando reporte diario Laura de {fecha_str}")

    leads = _obtener_leads_supabase(ayer_inicio, ayer_fin)
    metricas = _calcular_metricas(leads)

    mensaje = (
        f"📊 *REPORTE DIARIO LAURA EQUITY — {fecha_str}*\n\n"
        f"👥 Leads nuevos: *{metricas['total']}*\n\n"
        f"✅ Con licencia calificada: *{metricas['con_licencia']}*\n"
        f"📚 Sin licencia: *{metricas['sin_licencia']}*\n"
        f"📋 Licencia 214: *{metricas['lic_214']}*\n\n"
        f"🎯 Contratados: *{metricas['contratados']}*\n\n"
        f"📣 *Fuentes:*\n"
        f"   🌐 programaequity.com: *{metricas['desde_lovable']}*\n"
        f"   💬 WhatsApp Laura: *{metricas['desde_whatsapp']}*\n"
        f"   📋 GHL: *{metricas['desde_ghl']}*"
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

    logger.info(f"[REPORT] Generando reporte semanal Laura semana {semana_num}")

    leads = _obtener_leads_supabase(inicio_semana, ahora)
    metricas = _calcular_metricas(leads)
    semantica = _analisis_semantico_claude(leads, f"la semana {semana_num}")

    mensaje = (
        f"📈 *REPORTE SEMANAL LAURA EQUITY — Semana {semana_num}*\n"
        f"_{periodo_str}_\n\n"
        f"👥 Total leads: *{metricas['total']}*\n\n"
        f"✅ Con licencia calificada: *{metricas['con_licencia']}*\n"
        f"📚 Sin licencia: *{metricas['sin_licencia']}*\n"
        f"📋 Licencia 214: *{metricas['lic_214']}*\n"
        f"🎯 Contratados: *{metricas['contratados']}*\n\n"
        f"📣 *Fuentes:*\n"
        f"   🌐 programaequity.com: *{metricas['desde_lovable']}*\n"
        f"   💬 WhatsApp Laura: *{metricas['desde_whatsapp']}*\n"
        f"   📋 GHL: *{metricas['desde_ghl']}*\n\n"
        f"🧠 *ANÁLISIS:*\n{semantica}"
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

    logger.info(f"[REPORT] Generando reporte mensual Laura de {mes_str}")

    leads = _obtener_leads_supabase(inicio_mes, mes_anterior)
    metricas = _calcular_metricas(leads)
    semantica = _analisis_semantico_claude(leads, mes_str)

    mensaje = (
        f"📅 *REPORTE MENSUAL LAURA EQUITY — {mes_str.upper()}*\n\n"
        f"👥 Total leads: *{metricas['total']}*\n\n"
        f"✅ Con licencia calificada: *{metricas['con_licencia']}*\n"
        f"📚 Sin licencia: *{metricas['sin_licencia']}*\n"
        f"📋 Licencia 214: *{metricas['lic_214']}*\n"
        f"🎯 Contratados: *{metricas['contratados']}*\n\n"
        f"📣 *Fuentes:*\n"
        f"   🌐 programaequity.com: *{metricas['desde_lovable']}*\n"
        f"   💬 WhatsApp Laura: *{metricas['desde_whatsapp']}*\n"
        f"   📋 GHL: *{metricas['desde_ghl']}*\n\n"
        f"🧠 *ANÁLISIS DEL MES:*\n{semantica}"
    )

    _enviar_telegram(mensaje)


# ── Verificación de horario ────────────────────────────────────

def verificar_y_ejecutar():
    """
    Llamado desde heartbeat cada 5 minutos.
    Horarios Laura (diferentes a Sara para no solapar):
    - Diario:  9am ET, lunes a sábado
    - Semanal: domingos 8pm ET
    - Mensual: día 1 a las 9am ET
    """
    ahora = _ahora_et()
    hora = ahora.hour
    minuto = ahora.minute
    dia_semana = ahora.weekday()  # 0=lunes, 6=domingo
    dia_mes = ahora.day

    # Diario: 9am ET, lunes a sábado (horario diferente a Sara que es 8am)
    if hora == 9 and minuto < 5 and dia_semana < 6:
        ejecutar_diario()

    # Semanal: domingo a las 8pm ET (Sara es 7pm)
    if hora == 20 and minuto < 5 and dia_semana == 6:
        ejecutar_semanal()

    # Mensual: día 1 a las 9am ET (Sara es 8am)
    if dia_mes == 1 and hora == 9 and minuto < 5:
        ejecutar_mensual()
