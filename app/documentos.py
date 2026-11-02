"""
Archivos del usuario (Excel, Word, Markdown, texto): los convierte a Markdown, saca una lista de
pasos para seguirlos uno a uno y elige las partes que importan para cada pregunta.
Nada se guarda en el servidor: el archivo vive en la pestaña del navegador.
"""
import base64
import csv
import io
import re
from pathlib import Path

from app.buscador import tokenizar

MAX_BYTES = 3_000_000       # Vercel admite ~4,5 MB por petición (el archivo viaja en base64)
MAX_TEXTO = 60_000
MAX_FILAS = 400
MAX_PASOS = 40


class ErrorArchivo(Exception):
    pass


def _celda(v):
    return "" if v is None else str(v).replace("|", "/").replace("\n", " ").strip()


def _tabla_md(filas):
    filas = [[_celda(c) for c in f] for f in filas]
    filas = [f for f in filas if any(f)]
    if not filas:
        return ""
    ancho = max(map(len, filas))
    filas = [f + [""] * (ancho - len(f)) for f in filas]
    lineas = ["| " + " | ".join(filas[0]) + " |", "|" + " --- |" * ancho]
    lineas += ["| " + " | ".join(f) + " |" for f in filas[1:]]
    return "\n".join(lineas)


def _leer_excel(datos):
    from openpyxl import load_workbook

    libro = load_workbook(io.BytesIO(datos), read_only=True, data_only=True)
    partes = []
    for hoja in libro.worksheets:
        filas = []
        for i, fila in enumerate(hoja.iter_rows(values_only=True)):
            if i >= MAX_FILAS:
                break
            filas.append(list(fila))
        tabla = _tabla_md(filas)
        if tabla:
            partes.append(f"## {hoja.title}\n\n{tabla}")
    libro.close()
    return "\n\n".join(partes)


def _leer_word(datos):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(datos))
    partes = []
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}p"):
            p = Paragraph(el, doc)
            texto = p.text.strip()
            if not texto:
                continue
            estilo = (p.style.name or "").lower() if p.style is not None else ""
            nivel = re.search(r"(?:heading|título|titulo)\s*(\d)", estilo)
            if nivel:
                partes.append("#" * min(int(nivel.group(1)) + 1, 4) + " " + texto)
            elif "list" in estilo or "lista" in estilo:
                partes.append("- " + texto)
            else:
                partes.append(texto)
        elif el.tag.endswith("}tbl"):
            tabla = _tabla_md([[c.text for c in f.cells] for f in Table(el, doc).rows])
            if tabla:
                partes.append(tabla)
    return "\n\n".join(partes)


def _leer_texto(datos):
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    raise ErrorArchivo("No se pudo leer el texto del archivo.")


def _leer_csv(datos):
    texto = _leer_texto(datos)
    try:
        dialecto = csv.Sniffer().sniff(texto[:2000], delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel
    return _tabla_md(list(csv.reader(io.StringIO(texto), dialecto))[:MAX_FILAS])


LECTORES = {".xlsx": _leer_excel, ".xlsm": _leer_excel, ".docx": _leer_word, ".md": _leer_texto,
            ".markdown": _leer_texto, ".txt": _leer_texto, ".csv": _leer_csv}


def _limpiar_paso(texto):
    texto = re.sub(r"^\s*(?:[-*+•]|\d{1,3}[.)-]|paso\s+\d+\s*[:.)-]?|\[[ xX]?\])\s*", "", texto, flags=re.I)
    texto = re.sub(r"^\s*(?:\[[ xX]?\])\s*", "", texto)
    return re.sub(r"[*_`#]", "", texto).strip()


def extraer_pasos(texto):
    """Lo más parecido a un paso a paso: numeración, casillas, títulos, viñetas o filas de tabla."""
    lineas = [l.rstrip() for l in texto.splitlines() if l.strip()]
    grupos = [
        [l for l in lineas if re.match(r"^\s*(?:\d{1,3}[.)-]\s|paso\s+\d+)", l, re.I)],
        [l for l in lineas if re.match(r"^\s*[-*+]\s+\[[ xX]?\]\s", l)],
        [l for l in lineas if re.match(r"^#{2,4}\s", l)],
        [l for l in lineas if re.match(r"^\s*[-*+•]\s", l)],
    ]
    for grupo in grupos:
        if len(grupo) >= 2:
            pasos = [_limpiar_paso(l) for l in grupo]
            return [p for p in pasos if p][:MAX_PASOS]
    filas = [l for l in lineas if l.startswith("|") and not re.match(r"^\|[\s\-|:]+$", l)]
    if len(filas) >= 2:
        pasos = []
        for f in filas[1:]:
            celdas = [c.strip() for c in f.strip("|").split("|") if c.strip()]
            if celdas:
                pasos.append(" — ".join(celdas[:3]))
        return pasos[:MAX_PASOS]
    return [_limpiar_paso(l)[:220] for l in lineas if len(l) > 3][:MAX_PASOS]


def procesar(nombre, contenido_b64):
    extension = Path(nombre).suffix.lower()
    if extension not in LECTORES:
        raise ErrorArchivo("Por ahora puedo leer Excel (.xlsx), Word (.docx), Markdown (.md), .txt y .csv.")
    try:
        datos = base64.b64decode(contenido_b64, validate=True)
    except ValueError:
        raise ErrorArchivo("El archivo llegó dañado. Intenta subirlo de nuevo.")
    if len(datos) > MAX_BYTES:
        raise ErrorArchivo("El archivo pesa más de 3 MB. Prueba con una versión más liviana.")
    try:
        texto = LECTORES[extension](datos)
    except ErrorArchivo:
        raise
    except Exception:
        raise ErrorArchivo("No pude abrir ese archivo. Revisa que no esté dañado ni protegido con contraseña.")
    texto = (texto or "").strip()
    if not texto:
        raise ErrorArchivo("El archivo no tiene texto que pueda leer.")
    recortado = len(texto) > MAX_TEXTO
    texto = texto[:MAX_TEXTO]
    return {"nombre": Path(nombre).name, "texto": texto, "pasos": extraer_pasos(texto), "recortado": recortado}


def extracto(texto, consulta, limite):
    """Inicio del documento más los bloques que mejor coinciden con la pregunta, sin pasarse del límite."""
    if len(texto) <= limite:
        return texto
    bloques = [b for b in re.split(r"\n{2,}", texto) if b.strip()]
    if len(bloques) < 3:
        # una tabla grande es un solo bloque: se trocea por líneas
        bloques = [b for b in texto.splitlines() if b.strip()]
    claves = set(tokenizar(consulta))
    orden = sorted(range(len(bloques)), key=lambda i: -len(claves & set(tokenizar(bloques[i]))))
    elegidos, usado = {0}, len(bloques[0])
    for i in orden:
        if usado + len(bloques[i]) > limite:
            continue
        elegidos.add(i)
        usado += len(bloques[i])
    return "\n\n".join(bloques[i][:limite] for i in sorted(elegidos))[:limite]
