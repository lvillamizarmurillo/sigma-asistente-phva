"""
Amplía la base con artículos complementarios de ACCESO ABIERTO (OpenAlex) sobre los temas
que más pregunta una empresa manufacturera: ISO 9001, estandarización, metalmecánica, etc.

Solo se guardan los que se pueden descargar libremente como PDF. Quedan con id 51, 52, …
en datos/complementarios.json y documentos/NN.pdf, y construir_indice.py los incluye.

Uso:  python scripts/ampliar_corpus.py [--max 40]
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
DIR_DOCS = RAIZ / "documentos"
SALIDA = RAIZ / "datos" / "complementarios.json"

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

CONSULTAS = [
    "ISO 9001 implementation small and medium manufacturing enterprises",
    "ISO 9001 certification benefits barriers manufacturing companies",
    "ISO 9001:2015 quality management system implementation case study",
    "standardization standard work manufacturing process improvement",
    "standard operating procedures quality manufacturing case study",
    "steel bar manufacturing quality defects improvement",
    "steel rolling mill quality control improvement",
    "metalworking industry quality management lean",
    "PDCA cycle quality improvement manufacturing case study",
    "5S implementation manufacturing productivity quality",
    "statistical process control implementation steel industry",
    "internal audit nonconformity corrective action quality management system",
    "kaizen standardization sustain continuous improvement manufacturing",
    "Six Sigma DMAIC metal manufacturing defects reduction",
    "total productive maintenance OEE manufacturing case study",
    "quality management practices operational performance manufacturing SMEs",
]

FASES = [
    ("Actuar", r"corrective action|8d|dmaic|six sigma|kaizen|continuous improvement|root cause"),
    ("Verificar", r"statistical process control|\bspc\b|audit|measurement|performance|control chart|inspection"),
    ("Hacer", r"implementation|5s|tpm|standard work|standard operating|standardi[sz]ation|lean"),
    ("Planificar", r"iso 9001|barrier|framework|critical success|readiness|planning|strategy"),
]


CALIDAD = r"quality|lean|six sigma|iso 9001|kaizen|standardi[sz]|standard work|standard operating|defect|pdca|5s|productive maintenance|statistical process|continuous improvement"
MANUFACTURA = r"manufactur|industr|production|factory|plant|steel|metal|sme|msme|enterprise|compan"
DESCARTAR = r"cow|milk|ayurved|swarnaprashana|patient|hospital|clinical|power quality|statcom|torque control|additive manufacturing"


def relevante(texto):
    t = texto.lower()
    return bool(re.search(CALIDAD, t)) and bool(re.search(MANUFACTURA, t)) and not re.search(DESCARTAR, t)


def resumen(inv):
    if not inv:
        return ""
    pos = {i: w for w, idxs in inv.items() for i in idxs}
    return " ".join(pos[i] for i in sorted(pos))


def fase_de(texto):
    t = texto.lower()
    conteo = {f: len(re.findall(p, t)) for f, p in FASES}
    return max(conteo, key=conteo.get) if any(conteo.values()) else "Hacer"


def bajar(url, destino):
    try:
        r = S.get(url, timeout=60)
    except requests.RequestException:
        return False
    if r.status_code == 200 and r.content[:5] == b"%PDF-" and len(r.content) > 50_000:
        destino.write_bytes(r.content)
        return True
    return False


def limpiar():
    """Quita de complementarios.json (y su PDF) lo que no pase el filtro de relevancia."""
    actuales = json.loads(SALIDA.read_text("utf-8")) if SALIDA.exists() else []
    quedan = []
    for a in actuales:
        if relevante(a["titulo"] + " " + a.get("resumen_en", "")):
            quedan.append(a)
        else:
            (DIR_DOCS / a["pdf"]).unlink(missing_ok=True)
            print(f"descartado [{a['id']}] {a['titulo'][:70]}")
    SALIDA.write_text(json.dumps(quedan, ensure_ascii=False, indent=2), "utf-8")
    print(f"{len(quedan)} complementarios relevantes")


def main():
    if "--limpiar" in sys.argv:
        return limpiar()
    maximo = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 40
    base = json.loads((RAIZ / "datos" / "metadatos.json").read_text("utf-8"))
    dois = {a["doi"].lower() for a in base}
    actuales = json.loads(SALIDA.read_text("utf-8")) if SALIDA.exists() else []
    dois |= {a["doi"].lower() for a in actuales}
    siguiente = max([50] + [a["id"] for a in actuales]) + 1
    titulos = {a["titulo"].lower()[:60] for a in base + actuales}

    for consulta in CONSULTAS:
        if len(actuales) >= maximo:
            break
        params = {
            "search": consulta,
            "filter": "open_access.is_oa:true,publication_year:2016-2026,type:article,has_abstract:true",
            "per_page": 25,
        }
        try:
            obras = S.get("https://api.openalex.org/works", params=params, timeout=40).json().get("results", [])
        except (requests.RequestException, ValueError):
            continue
        agregados = 0
        for w in obras:
            if agregados >= 3 or len(actuales) >= maximo:
                break
            doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            titulo = w.get("title") or ""
            if not doi or doi in dois or titulo.lower()[:60] in titulos or w.get("language") not in (None, "en", "es"):
                continue
            abstract = resumen(w.get("abstract_inverted_index"))
            if not relevante(titulo + " " + abstract):
                continue
            urls = [l.get("pdf_url") for l in [w.get("best_oa_location") or {}] + (w.get("locations") or []) if l and l.get("pdf_url")]
            destino = DIR_DOCS / f"{siguiente:02d}.pdf"
            if not any(bajar(u, destino) for u in dict.fromkeys(urls)):
                continue
            fuente = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
            actuales.append({
                "id": siguiente, "titulo": titulo, "anio": w.get("publication_year"), "revista": fuente,
                "doi": doi, "fase": fase_de(titulo + " " + abstract), "resumen_en": abstract,
                "autores": [a["author"]["display_name"] for a in w.get("authorships", [])[:6]],
                "palabras_clave_oa": [k["display_name"] for k in w.get("keywords", [])],
                "citas": w.get("cited_by_count", 0), "pdf": destino.name, "origen": "complementario",
                "consulta": consulta,
            })
            dois.add(doi)
            titulos.add(titulo.lower()[:60])
            print(f"[{siguiente}] {titulo[:80]}", flush=True)
            siguiente += 1
            agregados += 1
            SALIDA.write_text(json.dumps(actuales, ensure_ascii=False, indent=2), "utf-8")
        time.sleep(1)
    print(f"\n{len(actuales)} artículos complementarios -> {SALIDA}")


if __name__ == "__main__":
    main()
