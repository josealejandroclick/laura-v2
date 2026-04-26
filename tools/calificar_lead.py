"""
Tool: calificar_lead
Clasifica un lead del Programa EQUITY según su tipo de licencia.
Devuelve el tag correspondiente y el camino a seguir.
"""

import json

TOOL_SCHEMA = {
    "name": "calificar_lead",
    "description": (
        "Clasifica a un candidato del Programa EQUITY según su tipo de licencia de seguros. "
        "Devuelve el tag GHL correspondiente y las instrucciones del siguiente paso."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tiene_licencia": {
                "type": "boolean",
                "description": "True si la persona tiene licencia de seguros en EE.UU., False si no tiene."
            },
            "tipo_licencia": {
                "type": "string",
                "description": "Tipo de licencia: '215', '220', '240', '214', 'otra', o '' si no tiene.",
                "enum": ["215", "220", "240", "214", "otra", ""]
            },
            "nombre": {
                "type": "string",
                "description": "Nombre completo del candidato."
            }
        },
        "required": ["tiene_licencia", "tipo_licencia", "nombre"]
    }
}


def ejecutar(tiene_licencia: bool, tipo_licencia: str, nombre: str) -> str:
    """Clasifica el lead y devuelve JSON con tag y camino."""

    # Sin licencia
    if not tiene_licencia or tipo_licencia == "":
        return json.dumps({
            "clasificacion": "sin_licencia",
            "tag_principal": "equity_sin_lic",
            "tags_adicionales": ["lead_equity"],
            "camino": "escuela_licenciamiento",
            "mensaje_interno": (
                f"{nombre} NO tiene licencia. "
                "Derivar a Escuela de Licenciamiento MKAddesh. "
                "Necesita obtener licencia 215, 220 o 240."
            ),
            "siguiente_paso": (
                "Explicar la Escuela de Licenciamiento. "
                "Registrar en Supabase y GHL con tag equity_sin_lic. "
                "Notificar al equipo."
            )
        }, ensure_ascii=False)

    # Licencia 214 (solo vida — no califica para EQUITY directamente)
    if tipo_licencia == "214":
        return json.dumps({
            "clasificacion": "lic_214",
            "tag_principal": "lic_214",
            "tags_adicionales": ["lead_equity", "equity_sin_lic"],
            "camino": "escuela_licenciamiento",
            "mensaje_interno": (
                f"{nombre} tiene licencia 214 (solo vida). "
                "No califica directamente para EQUITY. "
                "Necesita agregar licencia 215, 220 o 240. "
                "Derivar a Escuela de Licenciamiento."
            ),
            "siguiente_paso": (
                "Explicar que con licencia 214 ya es agente, y que agregar la licencia de salud "
                "les abre una línea de negocio muy rentable. "
                "Registrar con tags lic_214 + equity_sin_lic. "
                "Notificar al equipo."
            )
        }, ensure_ascii=False)

    # Licencia de salud calificada (215, 220, 240)
    if tipo_licencia in ("215", "220", "240"):
        return json.dumps({
            "clasificacion": "con_licencia_calificada",
            "tag_principal": "equity_con_lic",
            "tags_adicionales": ["lead_equity", f"lic_{tipo_licencia}"],
            "camino": "formulario_contratacion",
            "mensaje_interno": (
                f"{nombre} tiene licencia {tipo_licencia}. "
                "CALIFICADO para el Programa EQUITY. "
                "Enviar al formulario de ingreso/contratación."
            ),
            "siguiente_paso": (
                "Felicitar al candidato. "
                "Explicar brevemente los beneficios de EQUITY. "
                "Registrar en Supabase y GHL con tag equity_con_lic. "
                "Notificar al equipo inmediatamente."
            )
        }, ensure_ascii=False)

    # Otra licencia — escalar a humano
    return json.dumps({
        "clasificacion": "otra_licencia",
        "tag_principal": "lead_equity",
        "tags_adicionales": ["lead_equity", "revisar_manualmente"],
        "camino": "escalar_asesor",
        "mensaje_interno": (
            f"{nombre} tiene licencia tipo '{tipo_licencia}'. "
            "No está en las categorías estándar. "
            "Escalar a asesor humano para evaluación."
        ),
        "siguiente_paso": (
            "Informar al candidato que un asesor especializado lo contactará. "
            "Registrar y notificar al equipo para revisión manual."
        )
    }, ensure_ascii=False)
