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
    Usa emojis de planes como marcadores únicos para evitar
    cortes incorrectos cuando el texto menciona nombres de planes.
    """
    MARCADORES_CON_EMOJI = [
        'Plan Básico 🏥',
        'Medium Cover 🛡',
        'Full Cover 💎',
    ]

    tiene_planes = sum(1 for m in MARCADORES_CON_EMOJI if m in texto)

    if tiene_planes >= 2:
        # Unir líneas que no empiezan con marcador+emoji
        lineas = texto.split('\n')
        buffer = ''
        bloques = []

        for linea in lineas:
            strip = linea.strip()
            if not strip:
                if buffer:
                    bloques.append(buffer.strip())
                    buffer = ''
                continue

            es_marcador = any(strip.startswith(m) for m in MARCADORES_CON_EMOJI)
            es_cual = strip.startswith('¿Cuál')

            if (es_marcador or es_cual) and buffer:
                bloques.append(buffer.strip())
                buffer = strip
            elif (es_marcador or es_cual) and not buffer:
                buffer = strip
            else:
                buffer = (buffer + ' ' + strip).strip() if buffer else strip

        if buffer:
            bloques.append(buffer.strip())

        # Separar pregunta final pegada al último plan
        resultado = []
        for bloque in bloques:
            if '¿Cuál te llama más la atención?' in bloque:
                idx = bloque.find('¿Cuál te llama más la atención?')
                antes = bloque[:idx].strip()
                pregunta = bloque[idx:].strip()
                if antes:
                    resultado.append(antes)
                resultado.append(pregunta)
            else:
                resultado.append(bloque)

        mensajes = [m for m in resultado if m]
        if len(mensajes) > 1:
            return mensajes

    # Sin planes — separar pregunta final si existe
    if '¿Cuál te llama más la atención?' in texto:
        idx = texto.find('¿Cuál te llama más la atención?')
        antes = texto[:idx].strip()
        pregunta = texto[idx:].strip()
        if antes:
            return [antes, pregunta]
        return [pregunta]

    # Dividir por párrafos dobles
    if '\n\n' in texto:
        partes = [p.strip() for p in texto.split('\n\n') if p.strip()]
        if len(partes) > 1:
            return partes

    return [texto]




# ============================================================
# SALUDOS Y HANDLERS
# ============================================================

_bot = None
_loop = None

SALUDOS_INICIO = [
    "Hola, soy Sara de Mkaddesh 👋 ¿Tienes seguro médico o estás buscando opciones?",
    "Hola 👋 soy Sara de Mkaddesh. ¿Ya tienes cobertura médica o estás buscando?",
    "Hola, soy Sara de Mkaddesh. ¿Tienes seguro ahorita o estás sin cobertura?",
    "Hola 👋 Sara de Mkaddesh por aquí. ¿Tienes seguro médico o estás buscando uno?",
]




_bot = None
_loop = None

SALUDOS_INICIO = [
    "Hola, soy Sara de Mkaddesh 👋 ¿Tienes seguro médico o estás buscando opciones?",
    "Hola 👋 soy Sara de Mkaddesh. ¿Ya tienes cobertura médica o estás buscando?",
    "Hola, soy Sara de Mkaddesh. ¿Tienes seguro ahorita o estás sin cobertura?",
    "Hola 👋 Sara de Mkaddesh por aquí. ¿Tienes seguro médico o estás buscando uno?",
]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    eliminar_sesion(chat_id)
    registrar_actividad(chat_id)
    logger.info(f"Sesión reiniciada: {update.effective_user.first_name} (chat_id: {chat_id})")


async def cmd_nueva(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    eliminar_sesion(chat_id)
    registrar_actividad(chat_id)


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    info = obtener_info_sesion(chat_id)
    await update.message.reply_text(
        f"Sesión activa\nMensajes: {info.get('turnos', 0)}\nTamaño: {info.get('tamaño_kb', 0)} KB"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    texto = update.message.text
    nombre = update.effective_user.first_name or "Usuario"

    if not texto:
        return

    logger.info(f"[{chat_id}] {nombre}: {texto[:50]}...")
    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        agente = crear_agente()
        respuesta = procesar_mensaje(agente, chat_id, texto)

        respuesta_limpia = limpiar_markdown(respuesta)
        mensajes = dividir_en_mensajes(respuesta_limpia)

        for i, msg in enumerate(mensajes):
            if not msg.strip():
                continue
            # Delay proporcional al largo del mensaje que se va a enviar
            # Aplica a TODOS los mensajes incluyendo los siguientes
            chars = len(msg)
            delay_escritura = min(4.0, max(1.5, chars / 50))
            await update.effective_chat.send_action(ChatAction.TYPING)
            await asyncio.sleep(delay_escritura)
            await update.message.reply_text(msg)

        logger.info(f"[{chat_id}] Sara respondió ({len(mensajes)} msgs)")

    except Exception as e:
        logger.error(f"[{chat_id}] Error: {e}", exc_info=True)
        await update.message.reply_text("Disculpa, tuve un problema técnico. ¿Puedes repetir eso?")


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
