"""
Conocimiento adicional para responder con soluciones, no solo con artículos:
  - Catálogo de soluciones PHVA por problema típico (datos/soluciones.json)
  - Mapa de cláusulas ISO 9001 y ruta de certificación
  - Perfiles de sector (p. ej. metalmecánica)
  - Búsqueda en vivo de fuentes académicas reales en OpenAlex (gratis, sin clave)
"""
import json
import re
import time
from pathlib import Path

import httpx

from app.buscador import normalizar

RAIZ = Path(__file__).resolve().parent.parent
CATALOGO = json.loads((RAIZ / "datos" / "soluciones.json").read_text("utf-8"))


def _coincidencias(texto, sintomas):
    t = " " + " ".join(normalizar(texto).replace(",", " ").replace(".", " ").split()) + " "
    return sum(1 for s in sintomas if f" {normalizar(s)} " in t or (len(s) > 6 and normalizar(s) in t))


def problemas_relevantes(texto, maximo=3):
    puntajes = [(_coincidencias(texto, p["sintomas"]), p) for p in CATALOGO["problemas"]]
    return [p for c, p in sorted(puntajes, key=lambda x: -x[0]) if c > 0][:maximo]


def sector_relevante(texto):
    puntajes = [(_coincidencias(texto, s["sintomas"]), s) for s in CATALOGO["sectores"]]
    mejor = max(puntajes, key=lambda x: x[0], default=(0, None))
    return mejor[1] if mejor[0] > 0 else None


def bloque_soluciones(texto, detallado=True):
    """Texto compacto con las soluciones del catálogo que aplican a la pregunta/caso."""
    partes = []
    problemas = problemas_relevantes(texto, 3 if detallado else 1)
    for p in problemas:
        lineas = [f"### {p['nombre']}"]
        if detallado:
            lineas.append("Causas típicas: " + "; ".join(p["causas_tipicas"]))
        for fase, acciones in p["fases"].items():
            lineas.append(f"- {fase}: " + " | ".join(acciones if detallado else acciones[:2]))
        lineas.append("Herramientas: " + ", ".join(p["herramientas"]))
        lineas.append("Indicadores: " + ", ".join(p["indicadores"]))
        lineas.append("Cláusulas ISO 9001 relacionadas: " + ", ".join(p["iso9001"]))
        lineas.append("Artículos que lo respaldan: " + ", ".join(f"[{n}]" for n in p["articulos"]))
        partes.append("\n".join(lineas))
    if any(p["id"] == "certificacion_iso9001" for p in problemas) or "iso" in normalizar(texto):
        iso = CATALOGO["iso9001"]
        lineas = ["### ISO 9001:2015 — requisitos por fase PHVA"]
        for c in iso["clausulas"]:
            lineas.append(f"- Cláusula {c['num']} {c['titulo']} ({c['fase']}): {c['pide']} Evidencias: {', '.join(c['evidencias'])}.")
        if detallado:
            lineas.append("Ruta típica de certificación en una pyme: " + " ".join(iso["ruta_certificacion"]))
        partes.append("\n".join(lineas))
    sector = sector_relevante(texto)
    if sector:
        partes.append("\n".join([
            f"### Sector: {sector['nombre']}",
            "Defectos típicos: " + "; ".join(sector["defectos_tipicos"]),
            "Controles clave: " + "; ".join(sector["controles_clave"]),
            "Normas de referencia: " + "; ".join(sector["normas_referencia"]),
            "Artículos cercanos: " + ", ".join(f"[{n}]" for n in sector["articulos"]),
        ]))
    return "\n\n".join(partes), [p["nombre"] for p in problemas] + ([sector["nombre"]] if sector else [])


# ---------------------------------------------------------------- OpenAlex

_cache = {}
_CALIDAD = re.compile(r"quality|lean|six sigma|iso 9001|kaizen|standardi[sz]|standard work|standard operating|defect|pdca|5s|maintenance|statistical process|continuous improvement|audit|nonconform", re.I)
_VACIAS = {"with", "from", "that", "this", "into", "for", "and", "the", "case", "study", "studies", "best", "practices", "using", "based"}


def _pertinencia(w, consulta):
    """Cuántos términos de la consulta aparecen en título+resumen (0 si no trata de calidad)."""
    texto = ((w.get("title") or "") + " " + _abstract(w.get("abstract_inverted_index"))).lower()
    if not _CALIDAD.search(texto) or "additive manufactur" in texto or "3d print" in texto:
        return 0
    terminos = {t for t in re.findall(r"[a-z0-9.]{4,}", consulta.lower()) if t not in _VACIAS}
    return sum(1 for t in terminos if t.rstrip("s") in texto)


def _abstract(inv):
    if not inv:
        return ""
    pos = {i: w for w, idxs in inv.items() for i in idxs}
    return " ".join(pos[i] for i in sorted(pos))


async def buscar_en_linea(consultas, por_consulta=4, maximo=6, excluir_dois=()):
    """Busca artículos reales y recientes en OpenAlex. Devuelve [{etiqueta, titulo, anio, revista, doi, url, citas, resumen}]."""
    vistos, resultados = set(excluir_dois), []
    async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "AsistentePHVA/1.0"}) as c:
        for q in consultas[:3]:
            clave = q.lower().strip()
            if clave in _cache and time.time() - _cache[clave][0] < 3600:
                obras = _cache[clave][1]
            else:
                obras = []
                filtro = "publication_year:2015-2026,has_abstract:true,type:article|review"
                for params in ({"filter": f"title_and_abstract.search:{q.replace(',', ' ')},{filtro}", "per_page": 25, "sort": "relevance_score:desc"},
                               {"search": q, "filter": filtro, "per_page": 25}):
                    try:
                        r = await c.get("https://api.openalex.org/works", params=params)
                        obras += r.json().get("results", []) if r.status_code == 200 else []
                    except (httpx.HTTPError, ValueError):
                        pass
                    if len(obras) >= 6:
                        break
                _cache[clave] = (time.time(), obras)
            # pertinencia real (términos de la consulta + tema de calidad) y luego relevancia de OpenAlex;
            # las citas solo desempatan un poco
            obras = [(w, _pertinencia(w, q)) for w in obras]
            obras = [w for w, p in sorted(obras, key=lambda x: (-x[1], -(x[0].get("relevance_score") or 0) * (1 + min(x[0].get("cited_by_count", 0), 100) / 1000))) if p >= 2]
            n = 0
            for w in obras:
                doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
                if not doi or doi in vistos or n >= por_consulta:
                    continue
                vistos.add(doi)
                n += 1
                fuente = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
                resultados.append({
                    "titulo": w.get("title") or "", "anio": w.get("publication_year"), "revista": fuente,
                    "doi": doi, "url": f"https://doi.org/{doi}", "citas": w.get("cited_by_count", 0),
                    "acceso_abierto": bool((w.get("open_access") or {}).get("is_oa")),
                    "resumen": _abstract(w.get("abstract_inverted_index"))[:900],
                })
    resultados = resultados[:maximo]
    for i, r in enumerate(resultados, 1):
        r["etiqueta"] = f"W{i}"
    return resultados
