"""
Sara — Canal Telegram + Heartbeat
"""

import asyncio
import logging
import sys
import os
import re
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction

from config import (
    TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY,
    MODEL_ID, SOUL_FILE, AGENT_NAME
)
from sessions import obtener_info_sesion, eliminar_sesion
from sam_core import crear_agente, procesar_mensaje
from heartbeat import (
    Heartbeat,
    generar_mensaje_followup,
    registrar_actividad,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("sara_telegram")


# ============================================================
# UTILIDADES DE FORMATO
# ============================================================

def limpiar_markdown(texto: str) -> str:
    """Elimina markdown que Telegram renderiza como formato robótico."""
    texto = re.sub(r'\*\*(.*?)\*\*', r'\1', texto)
    texto = re.sub(r'__(.*?)__', r'\1', texto)
    texto = re.sub(r'\*([^*\n]+)\*', r'\1', texto)
    texto = re.sub(r'^#{1,6}\s+', '', texto, flags=re.MULTILINE)
    texto = re.sub(r'^\s*[-•]\s+', '', texto, flags=re.MULTILINE)
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    return texto.strip()


def dividir_en_mensajes(texto: str) -> list:
    """
    Divide la respuesta en mensajes separados.
    - Cada párrafo (separado por \n\n) va en su propio mensaje
    - Los planes van siempre en mensajes separados
    - Nunca un mensaje supera 500 caracteres si tiene párrafos separables
    """
    marcadores = ['Plan Básico', 'Medium Cover', 'Full Cover']
    tiene_planes = sum(1 for m in marcadores if m in texto)

    # Planes: dividir en cada marcador
    if tiene_planes >= 2:
        partes = re.split(r'(?=\bPlan Básico\b|\bMedium Cover\b|\bFull Cover\b)', texto)
        mensajes = [p.strip() for p in partes if p.strip()]
        if len(mensajes) > 1:
            return mensajes

    # Dividir siempre por párrafos dobles
    if '\n\n' in texto:
        partes = [p.strip() for p in texto.split('\n\n') if p.strip()]
        if len(partes) > 1:
            return partes

    # Dividir por salto de línea simple si hay más de una línea
    if '\n' in texto:
        partes = [p.strip() for p in texto.split('\n') if p.strip()]
        if len(partes) > 1:
            return partes

    return [texto]


def _enviar_async(chat_id: str, texto: str):
    if _bot and _loop:
        future = asyncio.run_coroutine_threadsafe(
            _bot.send_message(chat_id=int(chat_id), text=texto),
            _loop
        )
        try:
            future.result(timeout=10)
        except Exception as e:
            logger.error(f"Error enviando mensaje a {chat_id}: {e}")


def on_followup(session_id: str, followup_num: int):
    mensaje = generar_mensaje_followup(followup_num, AGENT_NAME)
    logger.info(f"💓 Follow-up #{followup_num} a {session_id}")
    _enviar_async(session_id, mensaje)


def on_cron(tarea: dict):
    session_id = tarea.get("session_id", "")
    tipo = tarea.get("tipo", "")
    descripcion = tarea.get("descripcion", "")

    logger.info(f"⏰ Cron: {tipo} - {descripcion}")

    if tipo == "recordatorio" and session_id:
        _enviar_async(session_id, "Oye, quería saber si tienes alguna duda sobre lo que hablamos. Aquí estoy 😊")

    elif tipo == "followup" and session_id:
        try:
            agente = crear_agente()
            respuesta = procesar_mensaje(
                agente, session_id,
                f"[SISTEMA: Envía un mensaje de follow-up. Contexto: {descripcion}]"
            )
            _enviar_async(session_id, respuesta)
        except Exception as e:
            logger.error(f"Error en cron followup: {e}")

    elif tipo == "notificacion":
        from config import NOTIFY_CHAT_ID
        if NOTIFY_CHAT_ID:
            _enviar_async(NOTIFY_CHAT_ID, f"📋 {descripcion}")


# ============================================================
# ARRANQUE
# ============================================================

def main():
    global _bot, _loop

    if not TELEGRAM_BOT_TOKEN:
        print("❌ Falta TELEGRAM_BOT_TOKEN en .env")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        print("❌ Falta ANTHROPIC_API_KEY en .env")
        sys.exit(1)

    print(f"""
╔══════════════════════════════════════════╗
║  {AGENT_NAME} — Telegram + Heartbeat
║  Soul: {SOUL_FILE}
║  Modelo: {MODEL_ID}
║  Heartbeat: activo
║  Bot lista. Esperando mensajes...
╚══════════════════════════════════════════╝
    """)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    _bot = app.bot

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("nueva", cmd_nueva))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    heartbeat = Heartbeat(on_followup=on_followup, on_cron=on_cron)

    async def post_init(application):
        global _loop
        _loop = asyncio.get_event_loop()
        heartbeat.iniciar()
        logger.info("💓 Heartbeat iniciado")

    app.post_init = post_init

    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        heartbeat.detener()


if __name__ == "__main__":
    main()
