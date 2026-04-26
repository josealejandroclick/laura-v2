"""
Tool: notificar_equipo
Envía avisos a 2 grupos de Telegram + email soporte@mkaddeshcorp.com
cuando un lead es calificado por Laura.
"""

import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOL_SCHEMA = {
    "name": "notificar_equipo",
    "description": (
        "Envía una notificación al equipo de MKAddesh cuando un lead del Programa EQUITY "
        "ha sido calificado. Notifica a 2 grupos de Telegram y al email de soporte."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "nombre": {
                "type": "string",
                "description": "Nombre completo del lead."
            },
            "telefono": {
                "type": "string",
                "description": "Teléfono del lead."
            },
            "email": {
                "type": "string",
                "description": "Email del lead."
            },
            "estado_usa": {
                "type": "string",
                "description": "Estado donde vive."
            },
            "clasificacion": {
                "type": "string",
                "description": "Resultado de calificación: con_licencia_calificada, sin_licencia, lic_214, otra_licencia.",
                "enum": ["con_licencia_calificada", "sin_licencia", "lic_214", "otra_licencia"]
            },
            "tipo_licencia": {
                "type": "string",
                "description": "Tipo de licencia si aplica."
            },
            "como_se_entero": {
                "type": "string",
                "description": "Fuente del lead."
            },
            "referido_por": {
                "type": "string",
                "description": "Nombre del referidor si aplica."
            },
            "tag_principal": {
                "type": "string",
                "description": "Tag principal asignado."
            }
        },
        "required": ["nombre", "telefono", "clasificacion", "tag_principal"]
    }
}


def _emoji_clasificacion(clasificacion: str) -> str:
    emojis = {
        "con_licencia_calificada": "🟢",
        "sin_licencia": "🟡",
        "lic_214": "🟠",
        "otra_licencia": "🔵",
    }
    return emojis.get(clasificacion, "⚪")


def _texto_clasificacion(clasificacion: str) -> str:
    textos = {
        "con_licencia_calificada": "✅ CON LICENCIA CALIFICADA — Listo para formulario de contratación",
        "sin_licencia": "📚 SIN LICENCIA — Derivar a Escuela de Licenciamiento",
        "lic_214": "📋 LICENCIA 214 (solo vida) — Derivar a Escuela para agregar licencia de salud",
        "otra_licencia": "🔍 LICENCIA NO ESTÁNDAR — Requiere revisión manual",
    }
    return textos.get(clasificacion, clasificacion)


def _construir_mensaje(nombre, telefono, email, estado_usa, clasificacion,
                        tipo_licencia, como_se_entero, referido_por, tag_principal) -> str:
    emoji = _emoji_clasificacion(clasificacion)
    clasificacion_texto = _texto_clasificacion(clasificacion)

    lineas = [
        f"{emoji} *NUEVO LEAD EQUITY — LAURA*",
        f"",
        f"👤 *Nombre:* {nombre}",
        f"📱 *Teléfono:* {telefono}",
    ]

    if email:
        lineas.append(f"📧 *Email:* {email}")
    if estado_usa:
        lineas.append(f"📍 *Estado:* {estado_usa}")

    lineas += [
        f"",
        f"🏷️ *Tag:* `{tag_principal}`",
        f"📊 *Clasificación:* {clasificacion_texto}",
    ]

    if tipo_licencia:
        lineas.append(f"📄 *Tipo licencia:* {tipo_licencia}")

    if como_se_entero:
        lineas.append(f"📣 *Fuente:* {como_se_entero}")

    if referido_por:
        lineas.append(f"🤝 *Referido por:* {referido_por}")

    lineas += [
        f"",
        f"_Notificación automática de Laura — Programa EQUITY_"
    ]

    return "\n".join(lineas)


def ejecutar(
    nombre: str,
    telefono: str,
    clasificacion: str,
    tag_principal: str,
    email: str = "",
    estado_usa: str = "",
    tipo_licencia: str = "",
    como_se_entero: str = "",
    referido_por: str = ""
) -> str:
    """Envía notificaciones a Telegram (2 grupos) y email."""

    resultados = {}
    mensaje = _construir_mensaje(
        nombre, telefono, email, estado_usa,
        clasificacion, tipo_licencia, como_se_entero,
        referido_por, tag_principal
    )

    # --- Telegram ---
    try:
        import requests
        from config import NOTIFY_BOT_TOKEN, NOTIFY_CHAT_ID_LAURA, NOTIFY_CHAT_ID_REPORTES, NOTIFY_CHAT_ID_REPORTES_2

        if NOTIFY_BOT_TOKEN:
            tg_base = f"https://api.telegram.org/bot{NOTIFY_BOT_TOKEN}/sendMessage"
            telegram_resultados = {}

            # Grupo 1: grupo nuevo de Laura
            if NOTIFY_CHAT_ID_LAURA:
                res = requests.post(tg_base, json={
                    "chat_id": NOTIFY_CHAT_ID_LAURA,
                    "text": mensaje,
                    "parse_mode": "Markdown"
                }, timeout=10)
                telegram_resultados["grupo_laura"] = {
                    "status": "ok" if res.status_code == 200 else "error",
                    "codigo": res.status_code
                }
            else:
                telegram_resultados["grupo_laura"] = {"status": "skip", "razon": "NOTIFY_CHAT_ID_LAURA no configurado"}

            # Grupo 2: grupo de reportes diarios (en horario diferente)
            if NOTIFY_CHAT_ID_REPORTES:
                res = requests.post(tg_base, json={
                    "chat_id": NOTIFY_CHAT_ID_REPORTES,
                    "text": mensaje,
                    "parse_mode": "Markdown"
                }, timeout=10)
                telegram_resultados["grupo_reportes"] = {
                    "status": "ok" if res.status_code == 200 else "error",
                    "codigo": res.status_code
                }
            else:
                telegram_resultados["grupo_reportes"] = {"status": "skip", "razon": "NOTIFY_CHAT_ID_REPORTES no configurado"}

            # Grupo 3: segundo destinatario de reportes (ID 2)
            if NOTIFY_CHAT_ID_REPORTES_2:
                res = requests.post(tg_base, json={
                    "chat_id": NOTIFY_CHAT_ID_REPORTES_2,
                    "text": mensaje,
                    "parse_mode": "Markdown"
                }, timeout=10)
                telegram_resultados["grupo_reportes_2"] = {
                    "status": "ok" if res.status_code == 200 else "error",
                    "codigo": res.status_code
                }
            else:
                telegram_resultados["grupo_reportes_2"] = {"status": "skip", "razon": "NOTIFY_CHAT_ID_REPORTES_2 no configurado"}

            resultados["telegram"] = telegram_resultados
        else:
            resultados["telegram"] = {"status": "skip", "razon": "NOTIFY_BOT_TOKEN no configurado"}

    except Exception as e:
        resultados["telegram"] = {"status": "error", "detalle": str(e)}

    # --- Email soporte@mkaddeshcorp.com via SMTP Gmail ---
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from config import EMAIL_FROM, NOTIFY_EMAIL

        email_password = os.getenv("EMAIL_APP_PASSWORD", "")

        if email_password:
            clasificacion_texto = _texto_clasificacion(clasificacion)
            asunto = f"[EQUITY] Nuevo lead: {nombre} — {clasificacion_texto[:40]}"

            lineas_cuerpo = [
                f"NUEVO LEAD EQUITY — LAURA",
                f"",
                f"Nombre: {nombre}",
                f"Teléfono: {telefono}",
                f"Email: {email or '—'}",
                f"Estado: {estado_usa or '—'}",
                f"Clasificación: {clasificacion_texto}",
                f"Tag: {tag_principal}",
                f"Tipo licencia: {tipo_licencia or '—'}",
                f"Fuente: {como_se_entero or '—'}",
                f"Referido por: {referido_por or '—'}",
                f"",
                f"Notificación automática de Laura — Programa EQUITY",
            ]
            cuerpo = "\n".join(lineas_cuerpo)

            msg = MIMEMultipart()
            msg["From"] = EMAIL_FROM
            msg["To"] = NOTIFY_EMAIL
            msg["Subject"] = asunto
            msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(EMAIL_FROM, email_password)
                server.sendmail(EMAIL_FROM, NOTIFY_EMAIL, msg.as_string())

            resultados["email"] = {
                "status": "ok",
                "destinatario": NOTIFY_EMAIL
            }
        else:
            resultados["email"] = {"status": "skip", "razon": "EMAIL_APP_PASSWORD no configurado"}

    except Exception as e:
        resultados["email"] = {"status": "error", "detalle": str(e)}

    return json.dumps({
        "notificaciones_enviadas": True,
        "nombre": nombre,
        "clasificacion": clasificacion,
        "resultados": resultados
    }, ensure_ascii=False)
