"""
Descarga los 50 artículos listados en Lista_50_articulos_PHVA.xlsx.

Para cada DOI:
  1. Consulta OpenAlex y Semantic Scholar (metadatos, resumen, enlaces de acceso abierto).
  2. Intenta descargar el PDF desde cada enlace candidato (y desde la meta
     etiqueta `citation_pdf_url` de la página de la revista).
  3. Guarda todo en datos/metadatos.json.

Los PDFs quedan en documentos/NN.pdf. Si algún artículo no se puede descargar
(revista de pago), basta con copiar el PDF manualmente como documentos/NN.pdf
y volver a correr construir_indice.py.

Uso:  python scripts/descargar.py
"""
import json
import re
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import openpyxl
import requests

RAIZ = Path(__file__).resolve().parent.parent
EXCEL = RAIZ / "Lista_50_articulos_PHVA.xlsx"
DIR_DOCS = RAIZ / "documentos"
DIR_DATOS = RAIZ / "datos"
SALIDA = DIR_DATOS / "metadatos.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


def leer_excel():
    ws = openpyxl.load_workbook(EXCEL).active
    filas = list(ws.iter_rows(values_only=True))
    articulos = []
    for n, fase, titulo, anio, revista, doi in filas[1:]:
        if not n:
            continue
        articulos.append({
            "id": int(n),
            "fase": fase,
            "titulo": titulo,
            "anio": anio,
            "revista": revista,
            "doi": str(doi).strip().lower(),
        })
    return articulos


def abstract_de_openalex(inv):
    if not inv:
        return ""
    pos = {}
    for palabra, idxs in inv.items():
        for i in idxs:
            pos[i] = palabra
    return " ".join(pos[i] for i in sorted(pos))


def consultar_openalex(doi):
    try:
        r = S.get(f"https://api.openalex.org/works/doi:{doi}", timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except requests.RequestException:
        return None


def consultar_semantic_scholar(doi):
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
        "?fields=abstract,openAccessPdf,tldr"
    )
    for _ in range(3):
        try:
            r = S.get(url, timeout=30)
        except requests.RequestException:
            return None
        if r.status_code == 429:
            time.sleep(3)
            continue
        return r.json() if r.status_code == 200 else None
    return None


def candidatos(oa, ss, doi):
    urls = []
    if oa:
        locs = [oa.get("best_oa_location")] + (oa.get("locations") or [])
        for loc in locs:
            if loc and loc.get("pdf_url"):
                urls.append(loc["pdf_url"])
        for loc in locs:
            if loc and loc.get("landing_page_url"):
                urls.append(loc["landing_page_url"])
    if ss and (ss.get("openAccessPdf") or {}).get("url"):
        urls.append(ss["openAccessPdf"]["url"])
    urls.append(f"https://doi.org/{doi}")
    vistos, unicos = set(), []
    for u in urls:
        if u and u not in vistos:
            vistos.add(u)
            unicos.append(u)
    return unicos


META_PDF = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)', re.I
)
META_PDF2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url', re.I
)


def bajar(url, destino, profundidad=0):
    """Descarga url; si es HTML busca citation_pdf_url y lo sigue una vez."""
    try:
        r = S.get(url, timeout=60, allow_redirects=True)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    if r.content[:5] == b"%PDF-":
        destino.write_bytes(r.content)
        return r.url
    if profundidad == 0 and "html" in r.headers.get("Content-Type", ""):
        m = META_PDF.search(r.text) or META_PDF2.search(r.text)
        if m:
            pdf = urljoin(r.url, unescape(m.group(1)))
            return bajar(pdf, destino, profundidad + 1)
    return None


def texto_europepmc(oa, destino_txt):
    """Si el artículo está en PubMed Central, baja el texto completo por la API oficial."""
    pmcid = ((oa or {}).get("ids") or {}).get("pmcid", "")
    pmcid = pmcid.rstrip("/").rsplit("/", 1)[-1]
    if not pmcid:
        return False
    if not pmcid.upper().startswith("PMC"):
        pmcid = "PMC" + pmcid
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    try:
        r = S.get(url, timeout=60)
    except requests.RequestException:
        return False
    if r.status_code != 200 or b"<body" not in r.content:
        return False
    cuerpo = r.text.split("<body", 1)[1]
    cuerpo = re.sub(r"<title>(.*?)</title>", r"\n\n\1\n", cuerpo, flags=re.S)
    cuerpo = re.sub(r"</p>", "\n", cuerpo)
    texto = unescape(re.sub(r"<[^>]+>", "", cuerpo))
    destino_txt.write_text(re.sub(r"\n{3,}", "\n\n", texto).strip(), "utf-8")
    return True


def main():
    DIR_DOCS.mkdir(exist_ok=True)
    DIR_DATOS.mkdir(exist_ok=True)
    previos = {}
    if SALIDA.exists():
        previos = {a["id"]: a for a in json.loads(SALIDA.read_text("utf-8"))}

    articulos = leer_excel()
    for a in articulos:
        destino = DIR_DOCS / f"{a['id']:02d}.pdf"
        prev = previos.get(a["id"], {})
        if prev.get("pdf") and (DIR_DOCS / prev["pdf"]).exists():
            a.update({k: prev[k] for k in prev if k not in a})
            a["pdf"] = prev["pdf"]
            print(f"[{a['id']:02d}] ya existe")
            continue
        if destino.exists() and prev.get("resumen_en") is not None:
            a.update({k: prev[k] for k in prev if k not in a})
            a["pdf"] = destino.name
            print(f"[{a['id']:02d}] ya existe")
            continue

        oa = consultar_openalex(a["doi"])
        ss = consultar_semantic_scholar(a["doi"])
        a["resumen_en"] = (
            abstract_de_openalex((oa or {}).get("abstract_inverted_index"))
            or (ss or {}).get("abstract")
            or ""
        )
        a["autores"] = [
            au["author"]["display_name"]
            for au in (oa or {}).get("authorships", [])[:6]
        ]
        a["palabras_clave_oa"] = [k["display_name"] for k in (oa or {}).get("keywords", [])]
        a["temas_oa"] = [t["display_name"] for t in (oa or {}).get("topics", [])[:3]]
        a["acceso_abierto"] = bool(((oa or {}).get("open_access") or {}).get("is_oa"))

        a["pdf"] = None
        if destino.exists():
            a["pdf"] = destino.name
        else:
            for url in candidatos(oa, ss, a["doi"]):
                origen = bajar(url, destino)
                if origen:
                    a["pdf"] = destino.name
                    a["pdf_origen"] = origen
                    break
        txt = destino.with_suffix(".txt")
        if not a["pdf"] and (txt.exists() or texto_europepmc(oa, txt)):
            a["pdf"] = txt.name
        estado = "PDF OK" if a["pdf"] else ("solo resumen" if a["resumen_en"] else "SIN DATOS")
        print(f"[{a['id']:02d}] {estado:13s} {a['titulo'][:70]}")
        time.sleep(1)

    SALIDA.write_text(json.dumps(articulos, ensure_ascii=False, indent=2), "utf-8")
    con_pdf = sum(1 for a in articulos if a["pdf"])
    print(f"\n{con_pdf}/{len(articulos)} PDFs descargados -> {DIR_DOCS}")
    faltan = [a for a in articulos if not a["pdf"]]
    if faltan:
        print("\nFaltan (copiarlos a mano como documentos/NN.pdf):")
        for a in faltan:
            print(f"  {a['id']:02d}.pdf  https://doi.org/{a['doi']}  {a['titulo'][:60]}")


if __name__ == "__main__":
    sys.exit(main())
