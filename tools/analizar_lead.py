"""
Tool: analizar_lead
Clasifica la temperatura del lead y envia notificacion al grupo de Telegram,
correo electronico, y nota interna en GHL asignada a Kriza.
"""

import json
import os
import httpx

NOTIFY_BOT_TOKEN = os.getenv("NOTIFY_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
NOTIFY_CHAT_ID = os.getenv("NOTIFY_CHAT_ID", "")

EMAIL_FROM = os.getenv("EMAIL_FROM", "mkaddeshholding@gmail.com")
EMAIL_TO = os.getenv("EMAIL_TO", "holdingventascrm@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")

GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"
GHL_KRIZA_ID = "kvk2jGxOZimsfr2hlL29"

TOOL_SCHEMA = {
    "name": "analizar_lead",
    "description": (
        "Clasifica al prospecto como CALIENTE, TIBIO o FRIO y notifica al equipo "
        "de ventas via Telegram, email y nota interna en GHL. Incluye resumen de la conversacion, plan de interes, "
        "y precios de cotizacion si ya se cotizo. "
        "Usar cuando el cliente haya mostrado interes claro o haya dado su nombre."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "temperatura": {
                "type": "string",
                "enum": ["CALIENTE", "TIBIO", "FRIO"],
                "description": (
                    "CALIENTE: quiere inscribirse, pide precio, da todos sus datos. "
                    "TIBIO: interesado pero con dudas o preguntas. "
                    "FRIO: solo explorando, no muestra urgencia."
                )
            },
            "nombre_lead": {
                "type": "string",
                "description": "Nombre del prospecto si lo dio"
            },
            "razon": {
                "type": "string",
                "description": "Por que se clasifica con esa temperatura"
            },
            "accion_sugerida": {
                "type": "string",
                "description": "Que debe hacer el asesor: llamar ahora, hacer seguimiento, esperar"
            },
            "plan_interes": {
                "type": "string",
                "enum": ["basico", "medium", "full", ""],
                "description": "Plan que mostro interes, si aplica"
            },
            "resumen_conversacion": {
                "type": "string",
                "description": "Resumen breve de los puntos clave de la conversacion"
            },
            "datos_cotizacion": {
                "type": "object",
                "description": (
                    "Datos de la cotizacion si ya se proceso: "
                    "zip, fpl_porcentaje, aptc_mensual, opciones_para_asesor "
                    "(basico_mensual, medium_mensual, full_mensual), mejor_plan"
                )
            },
            "chat_id": {
                "type": "string",
                "description": "ID del chat de Telegram del cliente"
            },
            "contacto_id": {
                "type": "string",
                "description": "ID del contacto en GHL para crear nota interna"
            }
        },
        "required": ["temperatura", "nombre_lead", "razon", "accion_sugerida"]
    }
}


def _enviar_email(asunto: str, cuerpo: str):
    if not EMAIL_PASSWORD:
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart()
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg["Subject"] = asunto
        msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    except Exception as e:
        print(f"[EMAIL] Error: {e}")


def _crear_nota_ghl(contacto_id: str, cuerpo: str):
    """Crea una nota interna en GHL en el contacto, asignada a Kriza."""
    if not GHL_API_KEY or not contacto_id:
        return
    try:
        headers = {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Version": "2021-04-15",
            "Content-Type": "application/json"
        }
        payload = {
            "body": cuerpo,
            "userId": GHL_KRIZA_ID
        }
        r = httpx.post(
            f"{GHL_BASE_URL}/contacts/{contacto_id}/notes",
            headers=headers,
            json=payload,
            timeout=8
        )
        if r.status_code not in (200, 201):
            print(f"[GHL NOTA] Error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[GHL NOTA] Error: {e}")


def ejecutar(
    temperatura: str,
    nombre_lead: str,
    razon: str,
    accion_sugerida: str,
    plan_interes: str = "",
    resumen_conversacion: str = "",
    datos_cotizacion: dict = None,
    chat_id: str = "",
    contacto_id: str = "",
    **kwargs
) -> str:
    emoji_map = {"CALIENTE": "CALIENTE", "TIBIO": "TIBIO", "FRIO": "FRIO"}
    emoji_icon = {"CALIENTE": "🔥", "TIBIO": "🌡", "FRIO": "❄️"}
    emoji = emoji_icon.get(temperatura, "❓")

    planes_nombres = {
        "full": "💎 Full Cover -- salud + hospitalizacion + accidente",
        "medium": "🛡️ Medium Cover -- salud + accidente",
        "basico": "🏥 Plan Basico -- solo salud"
    }

    lineas = [
        f"{emoji} *LEAD {temperatura}*",
        "",
        f"*Accion:* {accion_sugerida}",
        f"*Cliente:* {nombre_lead or 'No identificado'}",
        f"*Por que:* {razon}",
    ]

    if plan_interes:
        lineas.append(f"*Plan elegido:* {planes_nombres.get(plan_interes, plan_interes)}")

    if resumen_conversacion:
        lineas += ["", f"*Resumen:* {resumen_conversacion}"]

    if datos_cotizacion and isinstance(datos_cotizacion, str):
        try:
            import json as _json
            datos_cotizacion = _json.loads(datos_cotizacion)
        except Exception:
            datos_cotizacion = None

    if datos_cotizacion and isinstance(datos_cotizacion, dict):
        opciones = datos_cotizacion.get("opciones_para_asesor", {})
        mejor = datos_cotizacion.get("mejor_plan", {})
        if isinstance(mejor, str):
            mejor = {"nombre": mejor}
        fpl = datos_cotizacion.get("fpl_porcentaje", 0)
        aptc = datos_cotizacion.get("aptc_mensual", 0)
        csr = datos_cotizacion.get("csr", "")

        if opciones:
            lineas += ["", "*COTIZACION:*"]
            if mejor:
                issuer = mejor.get("issuer", "N/A")
                lineas += [
                    f"Plan: {mejor.get('nombre', 'N/A')[:45]}",
                    f"Compania: {issuer}",
                    f"Con subsidio: *${int(mejor.get('precio_con_subsidio', 0))}/mes*",
                    f"Deducible: ${int(mejor.get('deducible', 0)):,} | Max bolsillo: ${int(mejor.get('moop', 0)):,}",
                    f"FPL: {fpl}% | APTC: ${int(aptc)}/mes" + (f" | {csr}" if csr else ""),
                ]
            lineas += [
                "",
                "*OPCIONES PARA EL ASESOR:*",
                f"Basico (solo salud): *${opciones.get('basico_mensual', 0)}/mes*",
                f"Medium (salud + accidente): *${opciones.get('medium_mensual', 0)}/mes*",
                f"Full Cover (salud + hosp + accidente): *${opciones.get('full_mensual', 0)}/mes*",
            ]

    if chat_id:
        lineas += ["", f"Chat Telegram: `{chat_id}`"]

    mensaje = "\n".join(lineas)

    # 1. Telegram
    enviado = False
    if NOTIFY_BOT_TOKEN and NOTIFY_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{NOTIFY_BOT_TOKEN}/sendMessage"
            r = httpx.post(url, json={
                "chat_id": NOTIFY_CHAT_ID,
                "text": mensaje,
                "parse_mode": "Markdown"
            }, timeout=8)
            enviado = r.status_code == 200
        except Exception as e:
            print(f"[NOTIF] Error Telegram: {e}")

    # 2. Email
    emoji_texto = {"CALIENTE": "🔥 LEAD CALIENTE", "TIBIO": "🌡 LEAD TIBIO", "FRIO": "❄️ LEAD FRIO"}
    asunto = f"{emoji_texto.get(temperatura, temperatura)} -- {nombre_lead or 'Sin nombre'} | MKAddesh"
    _enviar_email(asunto, mensaje)

    # 3. Nota interna en GHL para Kriza
    if contacto_id:
        nota_cuerpo = (
            f"LEAD {temperatura} -- {nombre_lead or 'Sin nombre'}\n"
            f"Accion: {accion_sugerida}\n"
            f"Por que: {razon}\n"
        )
        if resumen_conversacion:
            nota_cuerpo += f"Resumen: {resumen_conversacion}\n"
        if plan_interes:
            nota_cuerpo += f"Plan de interes: {plan_interes}\n"
        _crear_nota_ghl(contacto_id, nota_cuerpo)

    # 4. Actualizar Supabase dashboard
    if _actualizar_lead and chat_id:
        try:
            campos_dashboard = {
                "temperatura": temperatura.lower(),
                "estado": "en_seguimiento" if temperatura in ("TIBIO", "FRIO") else "agendado",
            }
            if nombre_lead:
                campos_dashboard["nombre"] = nombre_lead
            if plan_interes:
                campos_dashboard["plan_interes"] = plan_interes
            if datos_cotizacion and isinstance(datos_cotizacion, dict):
                campos_dashboard["datos_cotizacion"] = datos_cotizacion
                if datos_cotizacion.get("zip"):
                    campos_dashboard["zip"] = datos_cotizacion["zip"]
            _actualizar_lead(chat_id, **campos_dashboard)
        except Exception as e:
            print(f"[ANALIZAR] Error actualizando dashboard: {e}")

    return json.dumps({
        "exito": True,
        "temperatura": temperatura,
        "notificacion_enviada": enviado,
        "mensaje": f"Lead {nombre_lead} clasificado como {temperatura}."
    }, ensure_ascii=False)
