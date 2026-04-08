"""
SAM — Canal Webhook (WhatsApp, SMS, GHL, cualquier plataforma)
Etapa 5

Patrón claw0 s04-s05:
"Every platform differs, but they all produce the same InboundMessage."

Este es un servidor HTTP mínimo que recibe webhooks de:
- GoHighLevel (WhatsApp, SMS, webchat de GHL)
- Integraciones custom (cualquier sistema que envíe POST)

Formato esperado del webhook entrante (POST JSON):
{
    "session_id": "whatsapp_+1234567890" o "ghl_contact_abc123",
    "texto": "Hola, necesito información sobre seguros",
    "nombre": "María García",        # opcional
    "telefono": "+1234567890",        # opcional
    "canal": "whatsapp",              # opcional: whatsapp, sms, webchat
    "reply_webhook": "https://..."    # URL para enviar la respuesta de vuelta
}

Formato de la respuesta que Sam devuelve al webhook:
{
    "session_id": "whatsapp_+1234567890",
    "respuesta": "¡Hola María! ¿En qué puedo ayudarle?",
    "metadata": { "herramientas_usadas": [...] }
}

INTEGRACIÓN CON GHL:
En GHL, creas un Workflow que:
1. Trigger: "Customer Replied" (WhatsApp/SMS)
2. Action: Webhook → POST a http://tu-servidor:8085/webhook
3. Espera la respuesta de Sam
4. Action: "Send WhatsApp/SMS" con el texto de Sam
"""

import json
import logging
import sys
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sam_core import crear_agente
from heartbeat import registrar_actividad, Heartbeat, generar_mensaje_followup
from config import AGENT_NAME, SOUL_FILE, MODEL_ID

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("sam_webhook")

# Puerto del servidor
PORT = int(os.getenv("WEBHOOK_PORT", "8085"))

# Cola de respuestas pendientes para reply_webhook
import httpx


class WebhookHandler(BaseHTTPRequestHandler):
    """
    Handler HTTP que procesa webhooks entrantes.
    
    Cada POST a /webhook es un mensaje de un usuario
    desde cualquier plataforma.
    """
    
    def do_POST(self):
        """Procesar mensaje entrante."""
        path = urlparse(self.path).path
        
        if path == "/webhook":
            self._handle_message()
        elif path == "/health":
            self._respond(200, {"status": "ok", "agent": AGENT_NAME})
        else:
            self._respond(404, {"error": "Not found"})
    
    def do_GET(self):
        """Health check y status."""
        path = urlparse(self.path).path
        
        if path == "/health":
            self._respond(200, {
                "status": "ok",
                "agent": AGENT_NAME,
                "soul": SOUL_FILE,
                "model": MODEL_ID
            })
        else:
            self._respond(404, {"error": "Not found"})
    
    def _handle_message(self):
        """
        Procesa un mensaje entrante via webhook.
        
        Flujo:
        1. Leer JSON del body
        2. Extraer session_id y texto
        3. Pasar al agent loop (sam_core)
        4. Devolver respuesta como JSON
        5. Si hay reply_webhook, enviar respuesta ahí también
        """
        try:
            # Leer body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
            
            # Extraer campos
            session_id = data.get("session_id", "")
            texto = data.get("texto", "") or data.get("message", "") or data.get("text", "")
            nombre = data.get("nombre", "") or data.get("name", "")
            canal = data.get("canal", "webhook")
            reply_webhook = data.get("reply_webhook", "")
            
            # Validar
            if not session_id or not texto:
                self._respond(400, {
                    "error": "Faltan campos requeridos: session_id y texto",
                    "ejemplo": {
                        "session_id": "whatsapp_+1234567890",
                        "texto": "Hola, necesito info sobre seguros"
                    }
                })
                return
            
            # Prefijo del canal para el session_id si no lo tiene
            if not session_id.startswith(("whatsapp_", "sms_", "ghl_", "web_")):
                session_id = f"{canal}_{session_id}"
            
            logger.info(f"[{canal}] [{session_id}] {nombre or 'Anónimo'}: {texto[:60]}...")
            
            # Registrar actividad para heartbeat
            registrar_actividad(session_id)
            
            # Procesar con Sam
            agente = crear_agente()
            inicio = time.time()
            respuesta = agente.procesar(session_id, texto).replace("\n", " ").strip()
            duracion = round(time.time() - inicio, 2)
            
            logger.info(f"[{session_id}] Sam respondió en {duracion}s ({len(respuesta)} chars)")
            
            # Respuesta al caller
            response_data = {
                "session_id": session_id,
                "respuesta": respuesta,
                "duracion_segundos": duracion,
                "metadata": {
                    "agent": AGENT_NAME,
                    "canal": canal,
                    "modelo": MODEL_ID
                }
            }
            self._respond(200, response_data)
            
            # Si hay reply_webhook, enviar la respuesta ahí también (async)
            if reply_webhook:
                threading.Thread(
                    target=self._enviar_reply_webhook,
                    args=(reply_webhook, response_data),
                    daemon=True
                ).start()
            
        except json.JSONDecodeError:
            self._respond(400, {"error": "JSON inválido"})
        except Exception as e:
            logger.error(f"Error procesando webhook: {e}", exc_info=True)
            self._respond(500, {"error": str(e)})
    
    def _enviar_reply_webhook(self, url: str, data: dict):
        """Envía la respuesta de Sam a un webhook externo (GHL, etc.)."""
        try:
            response = httpx.post(url, json=data, timeout=10.0)
            logger.info(f"Reply webhook enviado: {response.status_code}")
        except Exception as e:
            logger.error(f"Error enviando reply webhook: {e}")
    
    def _respond(self, status: int, data: dict):
        """Envía una respuesta JSON."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    
    def do_OPTIONS(self):
        """CORS preflight para webchat."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def log_message(self, format, *args):
        """Silenciar logs HTTP default, usamos nuestro logger."""
        pass


# ============================================================
# ARRANQUE
# ============================================================

def main():
    from config import ANTHROPIC_API_KEY
    
    if not ANTHROPIC_API_KEY:
        print("❌ Falta ANTHROPIC_API_KEY en .env")
        sys.exit(1)
    
    print(f"""
╔══════════════════════════════════════════╗
║  {AGENT_NAME} — Canal Webhook                
║  Soul: {SOUL_FILE}
║  Puerto: {PORT}
║  Modelo: {MODEL_ID}
║                                          
║  Endpoints:                              
║    POST /webhook  → recibir mensajes     
║    GET  /health   → status               
║                                          
║  Esperando webhooks...                   
╚══════════════════════════════════════════╝
    """)
    
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n👋 {AGENT_NAME} webhook server detenido.")
        server.server_close()


if __name__ == "__main__":
    main()
