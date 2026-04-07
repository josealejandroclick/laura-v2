"""
SAM — Sessions (Memoria Persistente)

Modo dual:
- Si SUPABASE_URL y SUPABASE_KEY están configuradas → usa Supabase (sara_v2_sesiones)
- Si no → usa archivos JSONL locales como fallback

Tabla Supabase: sara_v2_sesiones
"""

import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone

# ============================================================
# CONFIGURACIÓN
# ============================================================

SESSIONS_DIR = os.getenv("SESSIONS_DIR", "data/sessions")
MAX_TURNS = 40
KEEP_RECENT = 16

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_TABLE = "sara_v2_sesiones"

_supabase_client = None


def _get_supabase():
    global _supabase_client
    if _supabase_client:
        return _supabase_client
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        from supabase import create_client
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return _supabase_client
    except ImportError:
        print("[SESSIONS] supabase-py no instalado, usando JSONL")
        return None
    except Exception as e:
        print(f"[SESSIONS] Error conectando Supabase: {e}, usando JSONL")
        return None


def _usar_supabase() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# SERIALIZACIÓN
# ============================================================

def _serializar_content(content):
    if isinstance(content, list):
        result = []
        for bloque in content:
            if hasattr(bloque, "to_dict"):
                result.append(bloque.to_dict())
            elif hasattr(bloque, "model_dump"):
                result.append(bloque.model_dump())
            elif isinstance(bloque, dict):
                result.append(bloque)
            else:
                result.append(str(bloque))
        return result
    return content


# ============================================================
# JSONL (fallback local)
# ============================================================

def _session_path(session_id: str) -> Path:
    path = Path(SESSIONS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return path / f"{safe_id}.jsonl"


def _cargar_jsonl(session_id: str) -> list:
    archivo = _session_path(session_id)
    if not archivo.exists():
        return []
    mensajes = []
    with open(archivo, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                registro = json.loads(linea)
                mensajes.append({"role": registro["role"], "content": registro["content"]})
            except (json.JSONDecodeError, KeyError):
                continue
    return mensajes


def _guardar_jsonl(session_id: str, role: str, content) -> None:
    archivo = _session_path(session_id)
    registro = {"role": role, "content": _serializar_content(content), "ts": time.time()}
    with open(archivo, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def _eliminar_jsonl(session_id: str) -> bool:
    archivo = _session_path(session_id)
    if archivo.exists():
        archivo.unlink()
        return True
    return False


def _contar_jsonl(session_id: str) -> int:
    archivo = _session_path(session_id)
    if not archivo.exists():
        return 0
    with open(archivo, "r", encoding="utf-8") as f:
        return sum(1 for linea in f if linea.strip())


# ============================================================
# SUPABASE
# ============================================================

def _cargar_supabase(session_id: str) -> list:
    try:
        sb = _get_supabase()
        if not sb:
            return _cargar_jsonl(session_id)
        result = sb.table(SUPABASE_TABLE).select("mensajes").eq("session_id", session_id).execute()
        if result.data:
            return result.data[0].get("mensajes", [])
        return []
    except Exception as e:
        print(f"[SESSIONS] Error cargando Supabase: {e}")
        return _cargar_jsonl(session_id)


def _guardar_supabase(session_id: str, role: str, content) -> None:
    try:
        sb = _get_supabase()
        if not sb:
            _guardar_jsonl(session_id, role, content)
            return

        mensajes = _cargar_supabase(session_id)
        mensajes.append({"role": role, "content": _serializar_content(content), "ts": time.time()})

        sb.table(SUPABASE_TABLE).upsert({
            "session_id": session_id,
            "mensajes": mensajes,
            "ultimo_mensaje_en": _now_iso(),
            "actualizado_en": _now_iso()
        }).execute()

    except Exception as e:
        print(f"[SESSIONS] Error guardando Supabase: {e}")
        _guardar_jsonl(session_id, role, content)


def _eliminar_supabase(session_id: str) -> bool:
    try:
        sb = _get_supabase()
        if not sb:
            return _eliminar_jsonl(session_id)
        sb.table(SUPABASE_TABLE).delete().eq("session_id", session_id).execute()
        return True
    except Exception as e:
        print(f"[SESSIONS] Error eliminando Supabase: {e}")
        return _eliminar_jsonl(session_id)


def _contar_supabase(session_id: str) -> int:
    try:
        mensajes = _cargar_supabase(session_id)
        return len(mensajes)
    except Exception:
        return _contar_jsonl(session_id)


# ============================================================
# ACTUALIZAR CAMPOS DEL DASHBOARD
# ============================================================

def actualizar_lead(session_id: str, **campos) -> None:
    """
    Actualiza campos del lead en Supabase para el dashboard.
    Uso: actualizar_lead(session_id, nombre="María", temperatura="CALIENTE", estado="agendado")
    
    Campos disponibles:
    - nombre, ciudad, zip, ingreso_anual, num_personas, plan_interes
    - temperatura: frio / tibio / caliente
    - estado: conversando / agendado / en_seguimiento / no_respondio / descartado / llamado / cerrado
    - cita_agendada (bool), cita_hora (ISO string)
    - llamada_realizada (bool), venta_cerrada (bool)
    - fuente: organico / ad_dental / ad_embarazadas / ad_[nombre]
    - canal: telegram / whatsapp
    - datos_cotizacion (dict)
    """
    if not _usar_supabase():
        return
    try:
        sb = _get_supabase()
        if not sb:
            return
        campos["actualizado_en"] = _now_iso()
        sb.table(SUPABASE_TABLE).upsert({
            "session_id": session_id,
            **campos
        }).execute()
    except Exception as e:
        print(f"[SESSIONS] Error actualizando lead: {e}")


# ============================================================
# API PÚBLICA
# ============================================================

def cargar_sesion(session_id: str) -> list:
    if _usar_supabase():
        return _cargar_supabase(session_id)
    return _cargar_jsonl(session_id)


def guardar_mensaje(session_id: str, role: str, content) -> None:
    if _usar_supabase():
        _guardar_supabase(session_id, role, content)
    else:
        _guardar_jsonl(session_id, role, content)


def eliminar_sesion(session_id: str) -> bool:
    if _usar_supabase():
        return _eliminar_supabase(session_id)
    return _eliminar_jsonl(session_id)


def contar_turnos(session_id: str) -> int:
    if _usar_supabase():
        return _contar_supabase(session_id)
    return _contar_jsonl(session_id)


def necesita_compresion(session_id: str) -> bool:
    return contar_turnos(session_id) > MAX_TURNS


def comprimir_sesion(session_id: str, client, model: str, system_prompt: str) -> None:
    mensajes = cargar_sesion(session_id)
    if len(mensajes) <= KEEP_RECENT:
        return

    mensajes_viejos = mensajes[:-KEEP_RECENT]
    mensajes_recientes = mensajes[-KEEP_RECENT:]

    texto_para_resumir = ""
    for msg in mensajes_viejos:
        role = msg["role"]
        content = msg["content"]
        if isinstance(content, str):
            texto_para_resumir += f"{role}: {content}\n"
        elif isinstance(content, list):
            for bloque in content:
                if isinstance(bloque, dict):
                    if bloque.get("type") == "text":
                        texto_para_resumir += f"{role}: {bloque.get('text', '')}\n"
                    elif bloque.get("type") == "tool_use":
                        texto_para_resumir += f"{role}: [usó herramienta {bloque.get('name', '')}]\n"
                    elif bloque.get("type") == "tool_result":
                        texto_para_resumir += f"{role}: [resultado de herramienta]\n"

    try:
        resumen_response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=(
                "Eres un asistente que resume conversaciones. "
                "Resume la siguiente conversación entre un asistente de seguros y un prospecto. "
                "Incluye: nombre del prospecto, datos que dio (ZIP, edades, ingreso), "
                "planes cotizados, nivel de interés, y cualquier detalle importante. "
                "Sé conciso pero no pierdas datos clave."
            ),
            messages=[{"role": "user", "content": f"Resume esta conversación:\n\n{texto_para_resumir}"}]
        )
        resumen_texto = resumen_response.content[0].text
    except Exception as e:
        print(f"  ⚠️ Error al comprimir sesión: {e}")
        return

    mensajes_nuevos = [
        {
            "role": "user",
            "content": f"[CONTEXTO PREVIO DE ESTA CONVERSACIÓN]\n{resumen_texto}\n[FIN DEL CONTEXTO PREVIO]",
            "ts": time.time(),
            "compressed": True
        },
        {
            "role": "assistant",
            "content": "Entendido, tengo el contexto de nuestra conversación anterior. Continuemos.",
            "ts": time.time(),
            "compressed": True
        }
    ] + [{"role": m["role"], "content": m["content"], "ts": time.time()} for m in mensajes_recientes]

    if _usar_supabase():
        try:
            sb = _get_supabase()
            if sb:
                sb.table(SUPABASE_TABLE).upsert({
                    "session_id": session_id,
                    "mensajes": mensajes_nuevos,
                    "actualizado_en": _now_iso()
                }).execute()
        except Exception as e:
            print(f"[SESSIONS] Error comprimiendo en Supabase: {e}")
    else:
        archivo = _session_path(session_id)
        with open(archivo, "w", encoding="utf-8") as f:
            for msg in mensajes_nuevos:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    print(f"  📦 Sesión comprimida: {len(mensajes_viejos)} mensajes → resumen + {KEEP_RECENT} recientes")


def obtener_info_sesion(session_id: str) -> dict:
    turnos = contar_turnos(session_id)
    modo = "supabase" if _usar_supabase() else "jsonl"
    return {
        "session_id": session_id,
        "turnos": turnos,
        "modo": modo,
        "necesita_compresion": turnos > MAX_TURNS
    }
