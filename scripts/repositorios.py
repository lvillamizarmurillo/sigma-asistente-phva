"""
Segundo intento para los artículos sin PDF: busca copias en repositorios
institucionales (DSpace, bepress/Digital Commons, handle.net) listados por OpenAlex.

Uso:  python scripts/repositorios.py   (luego: python scripts/construir_indice.py)
"""
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

RAIZ = Path(__file__).resolve().parent.parent
DIR_DOCS = RAIZ / "documentos"
META = RAIZ / "datos" / "metadatos.json"

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

ENLACES_PDF = re.compile(r'href=["\']([^"\']*(?:/bitstream/[^"\']+|viewcontent\.cgi\?[^"\']+|/download/[^"\']+|\.pdf[^"\']*))["\']', re.I)
META_PDF = re.compile(r'name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)', re.I)


def es_pdf(url, destino):
    try:
        r = S.get(url, timeout=60)
    except requests.RequestException:
        return False
    if r.status_code == 200 and r.content[:5] == b"%PDF-":
        destino.write_bytes(r.content)
        return True
    return False


def probar_repositorio(url, destino):
    try:
        r = S.get(url, timeout=40)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    if r.content[:5] == b"%PDF-":
        destino.write_bytes(r.content)
        return r.url
    candidatos = META_PDF.findall(r.text) + ENLACES_PDF.findall(r.text)
    for c in dict.fromkeys(candidatos):
        pdf = urljoin(r.url, c.replace("&amp;", "&"))
        if es_pdf(pdf, destino):
            return pdf
    return None


def main():
    metadatos = json.loads(META.read_text("utf-8"))
    nuevos = 0
    for a in metadatos:
        if a.get("pdf"):
            continue
        oa = S.get(f"https://api.openalex.org/works/doi:{a['doi']}", timeout=30).json()
        repos = [
            l.get("landing_page_url") or l.get("pdf_url")
            for l in oa.get("locations", [])
            if (l.get("source") or {}).get("type") == "repository" or "handle.net" in (l.get("landing_page_url") or "")
        ]
        destino = DIR_DOCS / f"{a['id']:02d}.pdf"
        for url in filter(None, repos):
            origen = probar_repositorio(url, destino)
            if origen:
                a["pdf"], a["pdf_origen"] = destino.name, origen
                nuevos += 1
                print(f"[{a['id']:02d}] OK desde {origen[:80]}")
                break
        else:
            if repos:
                print(f"[{a['id']:02d}] sin PDF en {len(repos)} repositorio(s)")
    META.write_text(json.dumps(metadatos, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n{nuevos} PDFs nuevos")


if __name__ == "__main__":
    main()
