"""
Tool: consultar_conocimiento
Carga la base de conocimiento de Laura y devuelve la sección relevante
para responder una pregunta específica del candidato.

La base de conocimiento está en knowledge/laura_knowledge.md
Para actualizarla: editar ese archivo y hacer redeploy.
"""

import json
import os
from pathlib import Path


TOOL_SCHEMA = {
    "name": "consultar_conocimiento",
    "description": (
        "Consulta la base de conocimiento interna del Programa EQUITY de MKAddesh para obtener "
        "información correcta sobre: tipos de licencia, proceso de calificación, "
        "Escuela de Licenciamiento, beneficios del programa, Washington National, "
        "gerentes de línea, objeciones frecuentes, o cualquier detalle del programa. "
        "Usar SIEMPRE antes de responder preguntas sobre el programa, licencias "
        "o situaciones especiales del candidato."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pregunta": {
                "type": "string",
                "description": (
                    "La pregunta o tema a consultar. Ser específico. "
                    "Ejemplos: 'qué licencias califican para EQUITY', "
                    "'cómo funciona la escuela de licenciamiento', "
                    "'qué incluye el programa para agentes', "
                    "'cuándo es el overview', 'quiénes son los gerentes de línea'"
                )
            },
            "seccion": {
                "type": "string",
                "enum": [
                    "programa",
                    "licencias",
                    "escuela",
                    "flujo",
                    "gerentes",
                    "objeciones",
                    "prohibiciones",
                    "contacto",
                    "todo"
                ],
                "description": (
                    "Sección específica a consultar. Usar 'todo' si no estás seguro. "
                    "programa=qué es EQUITY y sus beneficios, "
                    "licencias=tipos de licencia y calificación, "
                    "escuela=proceso de licenciamiento, "
                    "flujo=registro y calificación de candidatos, "
                    "gerentes=líneas de negocio y gerentes, "
                    "objeciones=respuestas a objeciones frecuentes, "
                    "prohibiciones=qué nunca decir ni hacer, "
                    "contacto=información de contacto y recursos"
                )
            }
        },
        "required": ["pregunta"]
    }
}


# Mapeo de secciones a encabezados en el documento de Laura
SECTION_MAP = {
    "programa":     "## SECCIÓN 1",
    "licencias":    "## SECCIÓN 2",
    "escuela":      "## SECCIÓN 3",
    "flujo":        "## SECCIÓN 4",
    "gerentes":     "## SECCIÓN 5",
    "objeciones":   "## SECCIÓN 6",
    "prohibiciones":"## SECCIÓN 7",
    "contacto":     "## SECCIÓN 8",
    "todo":         None
}


def _cargar_knowledge() -> str:
    """Carga el archivo de conocimiento de Laura."""
    rutas = [
        Path(__file__).parent.parent / "knowledge" / "laura_knowledge.md",
        Path("/app/knowledge/laura_knowledge.md"),
        Path("knowledge/laura_knowledge.md"),
    ]
    for ruta in rutas:
        if ruta.exists():
            return ruta.read_text(encoding="utf-8")
    return ""


def _extraer_seccion(contenido: str, seccion: str) -> str:
    """Extrae una sección específica del documento."""
    if not seccion or seccion == "todo":
        return contenido

    encabezado = SECTION_MAP.get(seccion)
    if not encabezado:
        return contenido

    lineas = contenido.split("\n")
    dentro = False
    resultado = []

    for linea in lineas:
        if linea.startswith(encabezado):
            dentro = True
            resultado.append(linea)
            continue
        if dentro:
            # Terminar si encontramos el siguiente ## SECCIÓN
            if linea.startswith("## SECCIÓN") and linea != lineas[lineas.index(linea)]:
                break
            resultado.append(linea)

    return "\n".join(resultado).strip() if resultado else contenido


def _buscar_relevante(contenido: str, pregunta: str) -> str:
    """
    Busca párrafos relevantes para la pregunta dentro del contenido.
    Devuelve los bloques que contienen palabras clave de la pregunta.
    """
    palabras_clave = [
        p.lower() for p in pregunta.lower().split()
        if len(p) > 3 and p not in ["para", "como", "qué", "que", "una", "los", "las", "del"]
    ]

    if not palabras_clave:
        return contenido[:3000]

    bloques = contenido.split("\n\n")
    relevantes = []

    for bloque in bloques:
        bloque_lower = bloque.lower()
        coincidencias = sum(1 for palabra in palabras_clave if palabra in bloque_lower)
        if coincidencias > 0:
            relevantes.append((coincidencias, bloque))

    # Ordenar por relevancia y tomar los mejores
    relevantes.sort(key=lambda x: x[0], reverse=True)
    top = [b for _, b in relevantes[:6]]

    if top:
        return "\n\n".join(top)
    return contenido[:3000]


def ejecutar(pregunta: str, seccion: str = "todo") -> str:
    """
    Consulta la base de conocimiento de Laura y devuelve información relevante.
    """
    contenido = _cargar_knowledge()

    if not contenido:
        return json.dumps({
            "exito": False,
            "error": "Base de conocimiento no disponible",
            "respuesta": "Información no disponible en este momento. Escalar al equipo."
        }, ensure_ascii=False)

    # Extraer sección específica si se indicó
    seccion_contenido = _extraer_seccion(contenido, seccion)

    # Buscar párrafos relevantes para la pregunta
    resultado = _buscar_relevante(seccion_contenido, pregunta)

    return json.dumps({
        "exito": True,
        "pregunta": pregunta,
        "seccion_consultada": seccion,
        "informacion": resultado
    }, ensure_ascii=False)
