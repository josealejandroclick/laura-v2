"""
Laura — Configuración central.
Bot de reclutamiento EQUITY - MKAddesh.
Cada despliegue tiene su propio .env con sus credenciales.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- Claude API ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL_ID = os.getenv("MODEL_ID", "claude-opus-4-5")

# --- Identidad ---
AGENT_NAME = os.getenv("AGENT_NAME", "Laura")
SOUL_FILE = os.getenv("SOUL_FILE", "souls/laura_equity.md")

# --- Telegram (bot propio de Laura) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# --- Notificaciones: 2 grupos + email ---
# Grupo nuevo de Laura (avisos de leads calificados)
NOTIFY_CHAT_ID_LAURA = os.getenv("NOTIFY_CHAT_ID_LAURA", "")
# Grupo de reportes diarios existente (en horario diferente)
NOTIFY_CHAT_ID_REPORTES = os.getenv("NOTIFY_CHAT_ID_REPORTES", "")
# Bot token para enviar notificaciones
NOTIFY_BOT_TOKEN = os.getenv("NOTIFY_BOT_TOKEN", "")
# Email de soporte
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "soporte@mkaddeshcorp.com")

# --- GoHighLevel CRM - Subcuenta EQUITY Reclutamiento ---
GHL_WEBHOOK_URL = os.getenv("GHL_WEBHOOK_URL", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_API_KEY = os.getenv("GHL_API_KEY", "")

# --- Supabase (proyecto conectado a Lovable / programaequity.com) ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# --- SendGrid / SMTP para notificaciones email ---
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "laura@mkaddeshcorp.com")

# --- Sessions ---
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "data/sessions")

# --- Límites ---
MAX_CONVERSATION_TURNS = 50
MAX_TOKENS_RESPONSE = 4096

# --- Gerentes de línea EQUITY ---
GERENTES_LINEA = [
    "Jimmy Arenas",
    "Isidro González",
    "Jamie Varona",
    "Andy Salandy",
    "Daniel Pulido",
]

# --- Tags GHL EQUITY ---
TAG_LEAD_EQUITY = "lead_equity"
TAG_CON_LICENCIA = "equity_con_lic"
TAG_SIN_LICENCIA = "equity_sin_lic"
TAG_LIC_214 = "lic_214"
TAG_LIC_215 = "lic_215"
TAG_LIC_220 = "lic_220"
TAG_LIC_240 = "lic_240"
