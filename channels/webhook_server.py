"""
SAM -- Canal Webhook (WhatsApp, SMS, GHL, cualquier plataforma)
Etapa 5
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

PORT = int(os.getenv("WEBHOOK_PORT", "8085"))
GHL_API_KEY = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_BASE_URL = "https://services.leadconnectorhq.com"

TAGS_CLIENTE_CERRADO = {"p_cliente_cerrado", "cliente_dental"}

MENSAJE_ASISTENCIA = (
    "Hola! Veo que ya eres cliente de Mkaddesh. "
    "Para cualquier tema relacionado con tu cobertura actual, citas medicas o asistencia, "
    "comunicate con nuestro equipo de Atencion al Cliente directamente: "
    "https://wa.me/17866004310 "
    "Ellos podran ayudarte de inmediato. Este numero es el departamento de Inscripciones. "
    "Un placer haberte atendido!"
)

import httpx


def _extraer_telefono(session_id: str) -> str:
    """Extrae el numero de telefono del session_id."""
    for prefijo in ("whatsapp_", "sms_", "ghl_", "web_", "webhook_"):
        if session_id.startswith(prefijo):
            return session_id[len(prefijo):]
    return session_id


def _buscar_contacto_por_telefono(telefono: str) -> dict:
    """
    Busca contacto en GHL por telefono usando endpoint correcto.
    Retorna: { "contacto_id": "...", "tags": [...] }
    """
    resultado = {"contacto_id": "", "tags": []}

    if not GHL_API_KEY or not GHL_LOCATION_ID or not telefono:
        return resultado

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-04-15",
        "Content-Type": "application/json"
    }

    # Limpiar telefono para busqueda — remover prefijo de pais y caracteres especiales
    telefono_limpio = telefono.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    # Usar los ultimos 10 digitos para busqueda
    if len(telefono_limpio) > 10:
        telefono_query = telefono_limpio[-10:]
    else:
        telefono_query = telefono_limpio

    try:
        r = httpx.get(
            f"{GHL_BASE_URL}/contacts/",
            headers=headers,
            params={"locationId": GHL_LOCATION_ID, "query": telefono_query},
            timeout=8
        )
        if r.status_code == 200:
            contactos = r.json().get("contacts", [])
            if contactos:
                contacto = contactos[0]
                resultado["contacto_id"] = contacto.get("id", "")
                resultado["tags"] = contacto.get("tags", [])
                logger.info(f"[GHL] Contacto encontrado: {resultado['contacto_id']} | tags: {resultado['tags']}")
            else:
                logger.info(f"[GHL] No se encontro contacto para {telefono_query}")
        else:
            logger.warning(f"[GHL] Error buscando contacto {telefono_query}: {r.status_code}")
    except Exception as e:
        logger.warning(f"[GHL] Excepcion buscando contacto: {e}")

    return resultado


def _obtener_tags_por_id(contacto_id: str) -> set:
    """Obtiene los tags de un contacto por su ID."""
    if not GHL_API_KEY or not contacto_id:
        return set()

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-04-15",
        "Content-Type": "application/json"
    }

    try:
        r = httpx.get(
            f"{GHL_BASE_URL}/contacts/{contacto_id}",
            headers=headers,
            timeout=8
        )
        if r.status_code == 200:
            data = r.json()
            contacto = data.get("contact", data)
            return set(contacto.get("tags", []))
    except Exception as e:
        logger.warning(f"[GHL] Error obteniendo tags por ID: {e}")

    return set()


def _obtener_historial_ghl(contacto_id: str) -> str:
    """Obtiene los ultimos 10 mensajes del contacto en GHL."""
    if not GHL_API_KEY or not contacto_id:
        return ""

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-04-15",
        "Content-Type": "application/json"
    }

    try:
        r2 = httpx.get(
            f"{GHL_BASE_URL}/conversations/search",
            params={"contactId": contacto_id, "limit": 1},
            headers=headers,
            timeout=8
        )
        if r2.status_code == 200:
            convs = r2.json().get("conversations", [])
            if convs:
                conv_id = convs[0].get("id", "")
                if conv_id:
                    r3 = httpx.get(
                        f"{GHL_BASE_URL}/conversations/{conv_id}/messages",
                        params={"limit": 10},
                        headers=headers,
                        timeout=8
                    )
                    if r3.status_code == 200:
                        mensajes = r3.json().get("messages", {}).get("messages", [])
                        if mensajes:
                            lineas = []
                            for m in reversed(mensajes):
                                direccion = "Cliente" if m.get("direction") == "inbound" else "Sara"
                                cuerpo = m.get("body", "").strip()
                                if cuerpo:
                                    lineas.append(f"{direccion}: {cuerpo}")
                            return "\n".join(lineas[-10:])
    except Exception as e:
        logger.warning(f"[GHL] Error obteniendo historial: {e}")

    return ""


class WebhookHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/webhook":
            self._handle_message()
        elif path == "/health":
            self._respond(200, {"status": "ok", "agent": AGENT_NAME})
        else:
            self._respond(404, {"error": "Not found"})

    def do_GET(self):
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
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))

            session_id = data.get("session_id", "")
            texto = data.get("texto", "") or data.get("message", "") or data.get("text", "")
            nombre = data.get("nombre", "") or data.get("name", "")
            canal = data.get("canal", "webhook")
            reply_webhook = data.get("reply_webhook", "")
            contacto_id_payload = data.get("contacto_id", "")

            if not session_id or not texto:
                self._respond(400, {"error": "Faltan campos requeridos: session_id y texto"})
                return

            if not session_id.startswith(("whatsapp_", "sms_", "ghl_", "web_")):
                session_id = f"{canal}_{session_id}"

            logger.info(f"[{canal}] [{session_id}] {nombre or 'Anonimo'}: {texto[:60]}...")

            # Buscar contacto en GHL por telefono
            telefono = _extraer_telefono(session_id)
            datos_contacto = _buscar_contacto_por_telefono(telefono)
            contacto_id = datos_contacto.get("contacto_id", "") or contacto_id_payload
            tags = set(datos_contacto.get("tags", []))

            # Si la busqueda por telefono no trajo tags pero tenemos contacto_id, intentar por ID
            if contacto_id and not tags:
                tags = _obtener_tags_por_id(contacto_id)

            logger.info(f"[{session_id}] contacto_id={contacto_id} | tags={tags}")

            # Cliente cerrado -> responder con asistencia y terminar
            if tags & TAGS_CLIENTE_CERRADO:
                logger.info(f"[{session_id}] Cliente cerrado -> redirigiendo a asistencia")
                response_data = {
                    "session_id": session_id,
                    "respuesta": MENSAJE_ASISTENCIA,
                    "duracion_segundos": 0,
                    "metadata": {
                        "agent": AGENT_NAME,
                        "canal": canal,
                        "modelo": MODEL_ID,
                        "cliente_cerrado": True
                    }
                }
                self._respond(200, response_data)
                if reply_webhook:
                    threading.Thread(
                        target=self._enviar_reply_webhook,
                        args=(reply_webhook, response_data),
                        daemon=True
                    ).start()
                return

            # Inyectar historial como contexto si el contacto existe en GHL
            if contacto_id:
                historial = _obtener_historial_ghl(contacto_id)
                if historial:
                    texto = (
                        f"[Contexto de conversacion previa con este cliente:\n{historial}\n"
                        f"--- Mensaje actual ---]\n{texto}"
                    )
                    logger.info(f"[{session_id}] Historial GHL inyectado ({len(historial)} chars)")

            registrar_actividad(session_id)

            extra_context = {"contacto_id": contacto_id} if contacto_id else {}

            agente = crear_agente()
            inicio = time.time()
            respuesta = agente.procesar(session_id, texto, extra_context=extra_context).replace("\n", " ").strip()
            duracion = round(time.time() - inicio, 2)

            logger.info(f"[{session_id}] Sam respondio en {duracion}s ({len(respuesta)} chars)")

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

            if reply_webhook:
                threading.Thread(
                    target=self._enviar_reply_webhook,
                    args=(reply_webhook, response_data),
                    daemon=True
                ).start()

        except json.JSONDecodeError:
            self._respond(400, {"error": "JSON invalido"})
        except Exception as e:
            logger.error(f"Error procesando webhook: {e}", exc_info=True)
            self._respond(500, {"error": str(e)})

    def _enviar_reply_webhook(self, url: str, data: dict):
        try:
            response = httpx.post(url, json=data, timeout=10.0)
            logger.info(f"Reply webhook enviado: {response.status_code}")
        except Exception as e:
            logger.error(f"Error enviando reply webhook: {e}")

    def _respond(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _respond(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    from config import ANTHROPIC_API_KEY

    if not ANTHROPIC_API_KEY:
        print("Falta ANTHROPIC_API_KEY en .env")
        sys.exit(1)

    print(f"""
╔══════════════════════════════════════════╗
║  {AGENT_NAME} -- Canal Webhook
║  Soul: {SOUL_FILE}
║  Puerto: {PORT}
║  Modelo: {MODEL_ID}
║
║  Endpoints:
║    POST /webhook  -> recibir mensajes
║    GET  /health   -> status
║
║  Esperando webhooks...
╚══════════════════════════════════════════╝
    """)

    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{AGENT_NAME} webhook server detenido.")
        server.server_close()


if __name__ == "__main__":
    main()
