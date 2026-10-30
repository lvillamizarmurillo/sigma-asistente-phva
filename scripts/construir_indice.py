"""
Construye datos/indice.json (lo que usa la IA para buscar) y
datos/mapa_temas.json (vista legible: tema -> artículos, palabra clave -> artículos).

Lee:  datos/metadatos.json (de descargar.py) y documentos/NN.pdf | NN.txt
Uso:  python scripts/construir_indice.py            # índice
      python scripts/construir_indice.py --resumir  # además, resumen en español de cada artículo (usa la API del LLM)
"""
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import logging

from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.ERROR)

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from app.buscador import tokenizar  # noqa: E402

DIR_DOCS = RAIZ / "documentos"
DIR_DATOS = RAIZ / "datos"
TAM_FRAG, SOLAPE = 1200, 150

TEMAS = {
    "Lean manufacturing": r"\blean\b",
    "Six Sigma": r"six sigma|dmaic",
    "Lean Six Sigma": r"lean six sigma",
    "TQM / Calidad total": r"total quality management|\btqm\b",
    "ISO 9001 / Sistema de gestión de calidad": r"iso 9001|quality management system",
    "Calidad 4.0 / Industria 4.0": r"quality 4\.0|industry 4\.0|lean 4\.0",
    "Inteligencia artificial / Machine learning": r"artificial intelligence|machine learning|deep learning|\bai\b",
    "Control estadístico de procesos (SPC)": r"statistical process control|\bspc\b|control chart",
    "Kaizen / Mejora continua": r"kaizen|continuous improvement",
    "Análisis de causa raíz (Pareto, Ishikawa, 8D)": r"root cause|pareto|ishikawa|fishbone|\b8d\b",
    "TPM / Mantenimiento": r"total productive maintenance|\btpm\b",
    "Barreras y retos de implementación": r"barrier|obstacle",
    "Factores críticos de éxito": r"critical success factor|success factor",
    "PYMES": r"\bsmes?\b|\bmsmes?\b|small and medium",
    "Cero defectos": r"zero.defect",
    "Sostenibilidad": r"sustainab|green manufacturing",
    "Liderazgo y cultura organizacional": r"leadership|top management|organi[sz]ational culture",
    "Desempeño operacional": r"operational performance|business performance|productivity",
    "Costos de calidad y desperdicio": r"cost of quality|failure cost|\bwaste\b|rework|scrap",
    "Proveedores y cadena de suministro": r"supplier|supply chain",
    "Gestión del conocimiento": r"knowledge management|\bseci\b",
    "Ciclo PHVA / PDCA": r"\bpdca\b|plan.do.check.act|deming cycle",
    "Sector automotriz": r"automotive|automobile|car exhaust|vehicle",
    "Sector textil y confección": r"textile|apparel|garment",
    "Sector alimentos": r"\bfood\b|tempeh",
    "Sector calzado": r"footwear",
    "Sector plásticos": r"\bplastic",
    "Sector madera y muebles": r"\bwood|furniture",
    "Sector vidrio": r"\bglass\b",
    "Sector metalmecánico / forja": r"forging|\bmetal",
    "Sector relojería": r"\bwatch(es)?\b",
    "Sector militar / defensa": r"military",
}

PAISES = {
    "Nigeria": r"nigeria", "India": r"\bindia", "Tanzania": r"tanzania", "Serbia": r"serbia",
    "Colombia": r"colombia|cundinamarca", "Indonesia": r"indonesia", "Marruecos": r"morocc",
    "Sudáfrica": r"south africa|kwazulu", "Egipto": r"egypt", "Etiopía": r"ethiopia",
    "Rusia": r"russia", "Polonia": r"poland|polish", "Malasia": r"malaysia", "Brasil": r"brazil",
    "México": r"mexic", "Pakistán": r"pakistan", "Bangladesh": r"bangladesh", "Italia": r"\bital",
    "China": r"\bchin", "Turquía": r"turkey|t[uü]rkiye", "Vietnam": r"vietnam", "Ghana": r"ghana",
}


def limpiar(texto):
    texto = texto.replace("\x00", " ")
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n\n", texto)
    texto = re.sub(r"(?<!\n)\n(?!\n)", " ", texto)
    return texto.strip()


def cortar_referencias(texto):
    m = list(re.finditer(r"\n\s*(references|reference list|bibliography|referencias|daftar pustaka)\s*\n", texto, re.I))
    if m and m[-1].start() > len(texto) * 0.5:
        return texto[: m[-1].start()]
    return texto


def leer_documento(ruta):
    if ruta.suffix == ".txt":
        return ruta.read_text("utf-8")
    try:
        lector = PdfReader(str(ruta))
        return "\n\n".join((p.extract_text() or "") for p in lector.pages)
    except Exception as e:  # PDF dañado o cifrado
        print(f"   ! no se pudo leer {ruta.name}: {e}")
        return ""


def fragmentar(texto):
    oraciones = re.split(r"(?<=[.!?])\s+", texto)
    frags, actual = [], ""
    for o in oraciones:
        if len(actual) + len(o) > TAM_FRAG and actual:
            frags.append(actual.strip())
            actual = actual[-SOLAPE:] + " " + o
        else:
            actual += " " + o
    if len(actual.strip()) > 200:
        frags.append(actual.strip())
    return [f for f in frags if len(re.findall(r"[a-zA-Z]{3,}", f)) > 40]


def detectar(patrones, texto, minimo):
    t = texto.lower()
    return [n for n, p in patrones.items() if len(re.findall(p, t)) >= minimo]


def resumir_es(art, texto):
    from app.llm import completar

    extracto = texto if len(texto) < 7000 else texto[:4000] + "\n[...]\n" + texto[-3000:]
    prompt = (
        "Resume en español, en 5 a 7 frases, este artículo científico sobre gestión de calidad en manufactura: "
        "objetivo, contexto (país/sector), método, hallazgos principales (con cifras si las hay) y aporte práctico "
        "para el ciclo PHVA. Usa términos técnicos en español con el original en inglés entre paréntesis la primera vez. "
        "Sin introducciones ni títulos.\n\n"
        f"Título: {art['titulo']}\nResumen original: {art['resumen_en']}\n\nTexto:\n{extracto}"
    )
    for intento in range(4):
        try:
            return completar(prompt, max_tokens=700).strip()
        except Exception:
            if intento == 3:
                raise
            time.sleep(25)


def main():
    metadatos = json.loads((DIR_DATOS / "metadatos.json").read_text("utf-8"))
    if (DIR_DATOS / "complementarios.json").exists():
        metadatos += json.loads((DIR_DATOS / "complementarios.json").read_text("utf-8"))
    tablas = {}
    if (DIR_DATOS / "tablas_phva.json").exists():
        tablas = {int(k): v for k, v in json.loads((DIR_DATOS / "tablas_phva.json").read_text("utf-8")).items()}
    resumir = "--resumir" in sys.argv
    previo = {}
    if (DIR_DATOS / "indice.json").exists():
        previo = {a["id"]: a for a in json.loads((DIR_DATOS / "indice.json").read_text("utf-8"))["articulos"]}

    articulos, fragmentos, textos = [], [], {}
    for m in metadatos:
        n = m["id"]
        ruta = next((DIR_DOCS / f"{n:02d}{ext}" for ext in (".pdf", ".txt") if (DIR_DOCS / f"{n:02d}{ext}").exists()), None)
        cuerpo = cortar_referencias(limpiar(leer_documento(ruta))) if ruta else ""
        completo = len(cuerpo) > 3000
        art = {
            "id": n,
            "titulo": m["titulo"],
            "anio": m["anio"],
            "revista": m["revista"],
            "fase": m["fase"],
            "doi": m["doi"],
            "autores": m.get("autores", []),
            "texto_completo": completo,
            "resumen_en": m.get("resumen_en", ""),
            "origen": m.get("origen", "base"),
        }
        for campo in ("barrera", "conclusion", "solucion"):
            if tablas.get(n, {}).get(campo):
                art[campo] = tablas[n][campo]
        base = " ".join([m["titulo"], art["resumen_en"], " ".join(m.get("palabras_clave_oa", []))])
        todo = base + " " + cuerpo
        textos[n] = todo
        art["temas"] = sorted(set(detectar(TEMAS, base, 1) + (detectar(TEMAS, cuerpo, 4) if completo else [])))
        art["paises"] = detectar(PAISES, m["titulo"] + " " + art["resumen_en"], 1)
        art["palabras_clave"] = list(dict.fromkeys(k.lower() for k in m.get("palabras_clave_oa", [])))

        encabezado = f"{m['titulo']}. "
        if art["resumen_en"]:
            fragmentos.append({"articulo": n, "n": 0, "es_resumen": True, "texto": encabezado + "Abstract: " + art["resumen_en"]})
        if completo:
            for i, f in enumerate(fragmentar(cuerpo), 1):
                fragmentos.append({"articulo": n, "n": i, "texto": f})

        if resumir:
            if previo.get(n, {}).get("resumen_es"):
                art["resumen_es"] = previo[n]["resumen_es"]
            else:
                try:
                    art["resumen_es"] = resumir_es(art, cuerpo or todo)
                    print(f"   resumen ES [{n:02d}] listo")
                except Exception as e:
                    print(f"   ! resumen [{n:02d}] falló: {e}")
        elif previo.get(n, {}).get("resumen_es"):
            art["resumen_es"] = previo[n]["resumen_es"]
        if art.get("barrera") or art.get("solucion"):
            # curado por el equipo del proyecto (Tablas 1 y 2): la fuente más confiable en español
            partes = [f"Fase PHVA: {art['fase']}."]
            if art.get("barrera"):
                partes.append(f"Problema o barrera identificada: {art['barrera']}")
            if art.get("conclusion"):
                partes.append(f"Conclusión: {art['conclusion']}")
            if art.get("solucion"):
                partes.append(f"Solución propuesta: {art['solucion']}")
            fragmentos.append({"articulo": n, "n": -2, "es_resumen": True, "curado": True,
                               "texto": encabezado + " ".join(partes)})
        if art.get("resumen_es"):
            # en español: así la búsqueda también acierta con las palabras exactas de la pregunta
            fragmentos.append({"articulo": n, "n": -1, "es_resumen": True,
                               "texto": encabezado + "Resumen en español: " + art["resumen_es"]})
        articulos.append(art)
        print(f"[{n:02d}] {'completo' if completo else 'resumen ':8s} {len(cuerpo):>7} car.  temas: {', '.join(art['temas'][:4])}")

    # palabras clave automáticas por TF-IDF (bigramas), se suman a las de OpenAlex
    df = Counter()
    tfs = {}
    for n, t in textos.items():
        tfs[n] = Counter(x for x in tokenizar(t) if "_" in x)
        df.update(tfs[n].keys())
    for art in articulos:
        tf = tfs[art["id"]]
        puntaje = {t: c * math.log(len(textos) / df[t]) for t, c in tf.items() if c >= 2 and df[t] < len(textos) * 0.5}
        mejores = [t.replace("_", " ") for t, _ in sorted(puntaje.items(), key=lambda x: -x[1])[:10]]
        art["palabras_clave"] = list(dict.fromkeys(art["palabras_clave"] + mejores))[:25]

    indice = {"generado": date.today().isoformat(), "articulos": articulos, "fragmentos": fragmentos}
    (DIR_DATOS / "indice.json").write_text(json.dumps(indice, ensure_ascii=False), "utf-8")

    mapa = {"por_fase": defaultdict(list), "por_tema": defaultdict(list), "por_pais": defaultdict(list), "por_palabra_clave": defaultdict(list)}
    for a in articulos:
        mapa["por_fase"][a["fase"]].append(a["id"])
        for t in a["temas"]:
            mapa["por_tema"][t].append(a["id"])
        for p in a["paises"]:
            mapa["por_pais"][p].append(a["id"])
        for k in a["palabras_clave"]:
            mapa["por_palabra_clave"][k].append(a["id"])
    mapa["por_palabra_clave"] = {k: v for k, v in sorted(mapa["por_palabra_clave"].items()) if len(v) > 1 or True}
    mapa["articulos"] = {a["id"]: {"titulo": a["titulo"], "anio": a["anio"], "fase": a["fase"], "temas": a["temas"], "palabras_clave": a["palabras_clave"]} for a in articulos}
    (DIR_DATOS / "mapa_temas.json").write_text(json.dumps(mapa, ensure_ascii=False, indent=2), "utf-8")

    completos = sum(a["texto_completo"] for a in articulos)
    print(f"\n{len(articulos)} artículos ({completos} con texto completo), {len(fragmentos)} fragmentos -> datos/indice.json")


if __name__ == "__main__":
    main()
