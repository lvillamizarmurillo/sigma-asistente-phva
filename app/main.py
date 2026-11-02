"""
API de Sigma, asistente de gestión de calidad (ciclo PHVA).
  GET  /               interfaz de chat
  GET  /api/articulos  catálogo de artículos (50 base + complementarios)
  POST /api/chat       {pregunta, modo: rapido|profundo|consultor, web, historial, perfil} -> NDJSON en streaming

Modos:
  rapido     una búsqueda y respuesta breve basada en los artículos
  profundo   analiza la pregunta, busca con varias consultas, suma soluciones PHVA y fuentes en línea, razona más
  consultor  igual que profundo pero entrega un plan de solución PHVA para la empresa del usuario

Local:  python -m uvicorn app.main:app --reload
"""
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import conocimiento, llm
from app.buscador import Buscador, normalizar

RAIZ = Path(__file__).resolve().parent.parent
INDICE = json.loads((RAIZ / "datos" / "indice.json").read_text("utf-8"))
GLOSARIO = json.loads((RAIZ / "datos" / "glosario.json").read_text("utf-8"))["terminos"]
buscador = Buscador(INDICE, GLOSARIO)
N_BASE = sum(1 for a in INDICE["articulos"] if a.get("origen", "base") == "base")
N_COMPL = len(INDICE["articulos"]) - N_BASE

app = FastAPI(title="Sigma - Asistente PHVA de gestión de calidad")

# Presupuesto de contexto (caracteres) y de salida (tokens) según proveedor y modo.
# Groq gratis tiene ~8.000 tokens/minuto, por eso va más ajustado.
PRESUPUESTO = {
    "claude": {"rapido": (14000, 1500), "profundo": (34000, 5000), "consultor": (40000, 7000)},
    "groq": {"rapido": (8000, 1500), "profundo": (10500, 5000), "consultor": (11000, 6000)},
}

SISTEMA_BASE = f"""Te llamas Sigma y te creó Juan David Pachon Valero. Eres un consultor experto en gestión de calidad para la industria manufacturera, y conversas como una persona: con naturalidad, sin plantillas y sin rodeos.
Tu base de conocimiento es:
1) {N_BASE} artículos científicos (2021-2026) seleccionados por el equipo del proyecto para implementar el ciclo PHVA (Planificar-Hacer-Verificar-Actuar) con énfasis en la gerencia de calidad; de cada uno hay barrera, conclusión y, en muchos, la solución propuesta por el equipo.
2) {N_COMPL} artículos complementarios de acceso abierto (ISO 9001, estandarización, metalmecánica, SPC, TPM…).
3) Un catálogo de soluciones prácticas por problema y fase PHVA, el mapa de cláusulas de ISO 9001:2015 y perfiles de sector (guía práctica).
4) A veces, fuentes académicas recientes encontradas en línea en este momento (OpenAlex), etiquetadas [W1], [W2]…

Reglas:
- Responde SIEMPRE en español, aunque las fuentes estén en inglés. Traduce los términos técnicos y pon el original entre paréntesis la primera vez si ayuda.
- Cita con el número entre corchetes: artículos [12], fuentes en línea [W2]. Usa solo números que aparezcan en el contexto. Lo que venga del catálogo márcalo como (guía práctica) y lo que salga de tu conocimiento general como (conocimiento general).
- No inventes cifras, tolerancias, autores ni conclusiones. Si los datos de la empresa no alcanzan, haz supuestos explícitos y breves.
- Responde exactamente lo que te preguntan y en proporción: a una pregunta simple, una respuesta simple; no la conviertas en un informe.
- Sigue el hilo: entiende "eso", "lo anterior" o "en mi caso" según la conversación, usa el perfil de la empresa y no repitas lo ya dicho.
- Cuando te pidan ayuda con un problema real, piensa como consultor: conecta causas, soluciones concretas, herramientas, indicadores y la fase PHVA.
- Si varias fuentes se contradicen o la evidencia es débil (por ejemplo, un solo caso o solo el resumen), dilo en lugar de presentarlo como un hecho seguro.
- Markdown ligero: títulos cortos (##), viñetas y negritas. Tablas solo cuando el modo lo pida o el usuario compare cosas, con máximo 5 columnas cortas.
- Si la pregunta no tiene relación con calidad, manufactura u operaciones, dilo con amabilidad y redirige.
- No termines con preguntas ni ofrecimientos ("¿quieres que...?"): las sugerencias van aparte.

AL FINAL de cada respuesta escribe SIEMPRE una línea con exactamente <<<SUGERENCIAS>>> y debajo 3 preguntas de seguimiento, una por línea, sin numeración ni viñetas, de máximo 12 palabras, escritas como las haría el usuario, que avancen su caso o profundicen el tema."""

MODOS = {
    "rapido": """
MODO RÁPIDO:
- Responde como en una conversación: directo, en máximo ~120 palabras, sin títulos ni tablas; viñetas cortas solo si ayudan.
- Basa la respuesta en el contexto. Si falta información, dilo en una frase.""",
    "profundo": """
MODO PROFUNDO:
- Antes de escribir, razona: ¿qué pide realmente?, ¿qué dicen los artículos?, ¿coinciden o se contradicen?, ¿qué aplica al caso?
- Responde cubriendo las subpreguntas del análisis; compara y relaciona hallazgos entre artículos y fuentes en línea.
- Ubica cada hallazgo o recomendación en su fase PHVA.
- Cierra con una sección titulada "Aplicación práctica" (como título de nivel 2): 3-6 acciones concretas para una empresa manufacturera.""",
    "consultor": """
MODO CONSULTOR (plan de solución para la empresa del usuario). Estructura obligatoria:
## Diagnóstico
2-4 viñetas interpretando el caso (sector, problema raíz probable, riesgos). Supuestos explícitos si faltan datos.
## Plan de solución PHVA
Una tabla con columnas: Fase | Problema | Solución | Herramientas | Respaldo. Al menos 2 filas por fase (Planificar, Hacer, Verificar, Actuar). En «Respaldo» cita artículos [n], fuentes [Wn] o (guía práctica).
## Ruta ISO 9001 (solo si el caso habla de certificación o sistema de gestión)
Pasos con cláusulas y tiempos aproximados.
## Indicadores
4-6 indicadores con meta sugerida.
## Primeros 30 días
3-5 acciones de bajo costo y alto impacto.
Sé concreto y aplicable a la planta real del usuario; nada genérico.""",
}

MARCA = "<<<SUGERENCIAS>>>"
NO_ENTIENDO = "No te entiendo, ¿podrías ser más claro?"
SISTEMA_CHARLA = """Te llamas Sigma y te creó Juan David Pachon Valero. Eres un asistente de gestión de calidad para manufactura: respondes con artículos científicos, propones soluciones por fase PHVA y ayudas a planear mejoras e ISO 9001.
Responde en español, con naturalidad y en 1 a 3 frases, sin títulos ni listas. No cites artículos.
Si el tema no tiene que ver con calidad, manufactura u operaciones, dilo con amabilidad en una frase y comenta en qué sí puedes ayudar."""


def parece_ruido(texto):
    """Teclazos evidentes, sin gastar una llamada al modelo."""
    palabras = re.findall(r"[a-záéíóúüñ]+", texto.lower())
    if not palabras or sum(map(len, palabras)) < 2:
        return True
    return all(not re.search(r"[aeiouáéíóúü]", p) or re.search(r"[^aeiouáéíóúü]{5,}", p) for p in palabras)


PALABRAS_LISTA = ("que articulos", "cuales articulos", "cuales son los articulos", "lista", "listar", "enumera",
                  "cuantos articulos", "que estudios", "cuales estudios")


def linea(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


class Mensaje(BaseModel):
    rol: str
    texto: str = Field(max_length=12000)


class Consulta(BaseModel):
    pregunta: str = Field(min_length=1, max_length=3000)
    modo: str = "rapido"
    web: bool = False
    historial: list[Mensaje] = Field(default_factory=list, max_length=12)
    perfil: dict | None = None


def ficha(a):
    tipo = " · complementario" if a.get("origen") == "complementario" else ""
    return f"[{a['id']}] {a['titulo']} ({a['anio']}, {a['revista']}) — fase {a['fase']}{tipo}"


def recortar(texto, limite):
    return texto if len(texto) <= limite else texto[:limite].rsplit(" ", 1)[0] + "…"


def armar_contexto(pregunta, consultas, fase, presupuesto, soluciones_txt, web, profundo):
    partes, usado = [], 0

    if soluciones_txt:
        bloque = "GUÍA PRÁCTICA (catálogo de soluciones del proyecto):\n" + recortar(soluciones_txt, int(presupuesto * .32))
        partes.append(bloque)
        usado += len(bloque)

    frags = buscador.buscar_multi(consultas, k=14 if profundo else 6, fase=fase) if profundo \
        else buscador.buscar(consultas[0], k=6, fase=fase)
    ids, lineas_frag = [], ["FRAGMENTOS DE LOS ARTÍCULOS:"]
    limite_frag = presupuesto * (.5 if web else .62) if profundo else presupuesto - usado
    for f in frags:
        a = buscador.articulos[f["articulo"]]
        origen = "tabla del proyecto" if f.get("curado") else ("resumen" if f.get("es_resumen") else "texto completo")
        bloque = f"\n--- {ficha(a)} ({origen})\n{recortar(f['texto'], 1400)}"
        if sum(map(len, lineas_frag)) + len(bloque) > limite_frag:
            break
        lineas_frag.append(bloque)
        ids.append(f["articulo"])
    partes.append("\n".join(lineas_frag))

    if web:
        bloque = ["FUENTES EN LÍNEA (OpenAlex, consultadas ahora):"]
        for w in web:
            bloque.append(f"\n--- [{w['etiqueta']}] {w['titulo']} ({w['anio']}, {w['revista']}; {w['citas']} citas) doi:{w['doi']}\n{w['resumen']}")
        partes.append(recortar("\n".join(bloque), int(presupuesto * .25)))

    p = normalizar(pregunta)
    if fase:
        partes.append(f"\nARTÍCULOS DE LA FASE {fase.upper()}:\n" + "\n".join(
            ficha(a) for a in buscador.articulos.values() if a["fase"] == fase and a.get("origen", "base") == "base"))
    elif any(x in p for x in PALABRAS_LISTA):
        partes.append("\nARTÍCULOS MÁS RELACIONADOS (de mayor a menor):\n" + "\n".join(
            ficha(a) + f" — temas: {', '.join(a['temas'][:5])}" for a in buscador.articulos_relevantes(" ".join(consultas), k=12)))

    return "\n\n".join(partes), list(dict.fromkeys(ids))


@app.get("/")
def inicio():
    return FileResponse(RAIZ / "app" / "static" / "index.html")


@app.get("/api/salud")
def salud():
    return {"ok": True, "articulos": len(INDICE["articulos"]), "base": N_BASE, "complementarios": N_COMPL,
            "fragmentos": len(INDICE["fragmentos"]), "proveedor": llm.PROVEEDOR, "modelo": llm.MODELO}


@app.get("/api/uso")
async def uso():
    await llm.sondear_limites()
    return llm.estado_uso()


@app.get("/api/articulos")
def articulos():
    campos = ("id", "titulo", "anio", "revista", "fase", "doi", "temas", "texto_completo", "resumen_es",
              "barrera", "solucion", "origen")
    return [{k: a.get(k) for k in campos} for a in INDICE["articulos"]]


@app.post("/api/chat")
async def chat(c: Consulta):
    if not llm.CONFIGURADO:
        raise HTTPException(503, "El servidor no tiene configurada la clave del modelo (ANTHROPIC_API_KEY o GROQ_API_KEY).")
    modo = c.modo if c.modo in MODOS else "rapido"
    profundo = modo != "rapido"
    pregunta = c.pregunta.strip()
    ctx_max, out_max = PRESUPUESTO[llm.PROVEEDOR][modo]
    lim_hist = 4000 if llm.PROVEEDOR == "claude" else 1600
    historial = c.historial[-(10 if llm.PROVEEDOR == "claude" else 6):]
    hist_txt = "\n".join(f"{m.rol}: {m.texto[:600]}" for m in historial[-4:])

    async def eventos():
        perfil = c.perfil or {}
        if parece_ruido(pregunta):
            yield linea({"tipo": "directo"})
            yield linea({"tipo": "texto", "t": NO_ENTIENDO})
            return

        if profundo:
            yield linea({"tipo": "paso", "id": "analisis", "t": "Analizando tu caso"})
        plan = await llm.analizar(pregunta, perfil, hist_txt)
        tipo = plan.get("tipo", "consulta")

        if tipo == "sin_sentido":
            yield linea({"tipo": "directo"})
            yield linea({"tipo": "texto", "t": NO_ENTIENDO})
            return

        if isinstance(plan.get("perfil"), dict):
            nuevo = {k: v for k, v in plan["perfil"].items() if v and v not in ("?", "-", "desconocido", ["?"])}
            if nuevo:
                perfil = nuevo
                yield linea({"tipo": "perfil", "perfil": perfil})

        if tipo in ("conversacion", "fuera_de_tema"):
            yield linea({"tipo": "directo"})
            mensajes = [{"role": "system", "content": SISTEMA_CHARLA}]
            for m in historial[-4:]:
                mensajes.append({"role": "assistant" if m.rol == "ia" else "user", "content": m.texto[:800]})
            mensajes.append({"role": "user", "content": pregunta})
            try:
                async for trozo in llm.transmitir(mensajes, esfuerzo="low", max_tokens=500):
                    yield linea({"tipo": "texto", "t": trozo})
            except Exception as e:
                yield linea({"tipo": "error", "t": str(e)})
            return

        # Preguntar solo cuando pide ayuda o un plan, le falta algo indispensable y aún no se le preguntó
        ya_pregunto = historial and historial[-1].rol == "ia" and "?" in historial[-1].texto[-300:] and len(historial[-1].texto) < 700
        aclaraciones = [q for q in plan.get("aclaraciones", []) if isinstance(q, str) and q.strip()][:3]
        if tipo == "plan" and aclaraciones and not ya_pregunto:
            yield linea({"tipo": "directo"})
            intro = "Para ayudarte bien necesito saber un poco más:\n\n"
            yield linea({"tipo": "texto", "t": intro + "\n".join(f"- {q}" for q in aclaraciones)})
            return

        busqueda = plan.get("consulta_independiente") if isinstance(plan.get("consulta_independiente"), str) else ""
        busqueda = busqueda.strip() or pregunta
        fase = buscador.detectar_fase(busqueda)
        consultas_en = [q for q in plan.get("consultas_en", []) if isinstance(q, str)][:5]
        subpreguntas = [q for q in plan.get("subpreguntas", []) if isinstance(q, str)][:4]
        caso = " ".join([busqueda, json.dumps(perfil, ensure_ascii=False) if tipo == "plan" or profundo else "", " ".join(subpreguntas)])
        if profundo:
            yield linea({"tipo": "paso", "id": "analisis", "estado": "listo",
                         "t": "Caso analizado" + (f" · {perfil.get('sector')}" if perfil.get("sector") else "")})
        elif not consultas_en:
            consultas_en = [await llm.traducir_consulta(busqueda)]

        expansion = " ".join(buscador.expandir(caso))
        if profundo:
            consultas = [" ".join([busqueda, consultas_en[0] if consultas_en else "", expansion])] + consultas_en[1:]
        else:
            consultas = [" ".join([busqueda, " ".join(consultas_en), expansion])]
        if profundo:
            yield linea({"tipo": "paso", "id": "busqueda", "t": f"Buscando en {len(buscador.articulos)} artículos"})

        soluciones_txt, problemas = conocimiento.bloque_soluciones(caso, detallado=profundo)
        web = []
        if c.web or modo == "consultor":
            if profundo:
                yield linea({"tipo": "paso", "id": "web", "t": "Consultando fuentes académicas en línea"})
            dois = {a["doi"] for a in buscador.articulos.values()}
            web = await conocimiento.buscar_en_linea(consultas_en or [busqueda], por_consulta=2 if profundo else 3,
                                                     maximo=5 if profundo else 3, excluir_dois=dois)

        contexto, ids = armar_contexto(busqueda, consultas, fase, ctx_max, soluciones_txt, web, profundo)
        if profundo:
            yield linea({"tipo": "paso", "id": "busqueda", "estado": "listo", "t": f"{len(ids)} artículos relevantes"})
            if c.web or modo == "consultor":
                yield linea({"tipo": "paso", "id": "web", "estado": "listo", "t": f"{len(web)} fuentes en línea"})
            if problemas:
                yield linea({"tipo": "paso", "id": "soluciones", "estado": "listo", "t": "Soluciones: " + ", ".join(problemas[:3])})
            yield linea({"tipo": "paso", "id": "redaccion", "t": "Razonando y redactando" if modo == "profundo" else "Armando tu plan PHVA"})

        mensajes = [{"role": "system", "content": SISTEMA_BASE + MODOS[modo]}]
        for m in historial:
            mensajes.append({"role": "assistant" if m.rol == "ia" else "user", "content": m.texto[:lim_hist]})
        extra = ""
        if perfil:
            extra += f"PERFIL DE LA EMPRESA DEL USUARIO (de esta conversación): {json.dumps(perfil, ensure_ascii=False)}\n\n"
        if subpreguntas:
            extra += "PUNTOS QUE LA RESPUESTA DEBE TOCAR (intégralos con naturalidad, sin una sección aparte):\n- " + "\n- ".join(subpreguntas) + "\n\n"
        mensajes.append({"role": "user", "content": f"{contexto}\n\n{extra}PREGUNTA: {pregunta}"})

        texto, enviado, primero = "", 0, True
        try:
            esfuerzo = {"rapido": "low", "profundo": "high", "consultor": "high"}[modo]
            if llm.PROVEEDOR == "groq" and profundo:
                esfuerzo = "medium"   # en Groq el razonamiento sale del mismo cupo de tokens que la respuesta
            async for trozo in llm.transmitir(mensajes, esfuerzo=esfuerzo, max_tokens=out_max):
                if primero and profundo:
                    yield linea({"tipo": "paso", "id": "redaccion", "estado": "listo", "t": "Respuesta lista"})
                primero = False
                texto += trozo
                corte = texto.find(MARCA)
                visible = texto[:corte] if corte >= 0 else texto[:max(enviado, len(texto) - len(MARCA))]
                if len(visible) > enviado:
                    yield linea({"tipo": "texto", "t": visible[enviado:]})
                    enviado = len(visible)
        except Exception as e:
            yield linea({"tipo": "error", "t": str(e)})
            return
        respuesta, _, resto = texto.partition(MARCA)
        if len(respuesta) > enviado:
            yield linea({"tipo": "texto", "t": respuesta[enviado:]})
        sugerencias = [re.sub(r"^[\s\-*•\d.)]+", "", l).strip() for l in resto.splitlines()]
        yield linea({"tipo": "sugerencias", "items": [x for x in sugerencias if len(x) > 8][:3]})

        citados = [int(n) for n in re.findall(r"\[(\d{1,3})\]", respuesta)]
        fuentes = [i for i in dict.fromkeys(citados) if i in buscador.articulos] or ids[:6]
        datos = [{**{k: buscador.articulos[i].get(k) for k in ("id", "titulo", "anio", "fase", "doi", "origen")},
                  "citado": i in citados} for i in fuentes]
        web_citadas = set(re.findall(r"\[(W\d)\]", respuesta))
        datos_web = [{k: w[k] for k in ("etiqueta", "titulo", "anio", "revista", "url", "citas")} | {"citado": w["etiqueta"] in web_citadas}
                     for w in web if w["etiqueta"] in web_citadas or not web_citadas]
        yield linea({"tipo": "fuentes", "fuentes": datos, "web": datos_web})
        yield linea({"tipo": "uso", "uso": llm.estado_uso()})

    return StreamingResponse(eventos(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
