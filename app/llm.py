"""
Cliente del modelo de lenguaje. Elige el proveedor automáticamente:

  1. Claude (Anthropic)  si existe ANTHROPIC_API_KEY en .env   <- principal
  2. Groq (gratis)       si existe GROQ_API_KEY / LLM_API_KEY  <- respaldo

Variables (ver .env.example):
  ANTHROPIC_API_KEY      clave de https://console.anthropic.com
  CLAUDE_MODELO          claude-opus-5-5   (respuestas)
  CLAUDE_MODELO_RAPIDO   claude-haiku-5-5  (traduce la pregunta a términos de búsqueda)
  GROQ_API_KEY / LLM_API_KEY, LLM_BASE_URL, LLM_MODELO, LLM_MODELO_RESPALDO, LLM_MODELO_TRADUCTOR
"""
import json
import os
import re
import time
from datetime import date
from pathlib import Path

import httpx


def _cargar_env():
    ruta = Path(__file__).resolve().parent.parent / ".env"
    if ruta.exists():
        for linea in ruta.read_text("utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    os.environ.setdefault(k.strip(), v)


_cargar_env()

# --- Claude -----------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODELO = os.getenv("CLAUDE_MODELO", "claude-opus-5-5")
CLAUDE_MODELO_RAPIDO = os.getenv("CLAUDE_MODELO_RAPIDO", "claude-haiku-5-5")

# --- Compatible con OpenAI (Groq por defecto) ---------------------------------
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY", "")
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
MODELO_GROQ = os.getenv("LLM_MODELO", "openai/gpt-oss-120b")
MODELO_RESPALDO = os.getenv("LLM_MODELO_RESPALDO", "openai/gpt-oss-20b")
MODELO_TRADUCTOR = os.getenv("LLM_MODELO_TRADUCTOR", "openai/gpt-oss-20b")

PROVEEDOR = "claude" if ANTHROPIC_API_KEY else ("groq" if API_KEY else "")
MODELO = CLAUDE_MODELO if PROVEEDOR == "claude" else MODELO_GROQ
CONFIGURADO = bool(PROVEEDOR)

_claude = None
if PROVEEDOR == "claude":
    import anthropic

    _claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=120.0, max_retries=2)
    # beta de respaldo automático del lado del servidor si el modelo declina una petición
    _BETAS = ["server-side-fallback-2026-07-01"]


# =============================== Uso y límites =================================
# Groq informa en cada respuesta sus límites por minuto (tokens) y por día (consultas) y cuándo se reponen.
# Los tokens del día no vienen en las cabeceras: los cuenta Sigma (se reinician a medianoche, hora local).
LIMITE_TOKENS_DIA = int(os.getenv("GROQ_TOKENS_DIA", "200000"))
PRECIOS = {  # USD por millón de tokens (entrada, salida) para estimar el costo de Claude
    "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
}
_USO = {}


def _segundos(txt):
    """'1h33m36s' | '22.882s' | '690ms' -> segundos."""
    if not txt:
        return None
    total = 0.0
    for valor, unidad in re.findall(r"([\d.]+)(ms|h|m|s)", txt):
        total += float(valor) * {"h": 3600, "m": 60, "s": 1, "ms": .001}[unidad]
    return total


def _modelo_uso(modelo):
    hoy = date.today().isoformat()
    u = _USO.setdefault(modelo, {"fecha": hoy, "tokens_hoy": 0, "consultas_hoy": 0, "costo_hoy": 0.0})
    if u["fecha"] != hoy:
        u.update(fecha=hoy, tokens_hoy=0, consultas_hoy=0, costo_hoy=0.0)
    return u


def _registrar_cabeceras(modelo, h):
    if "x-ratelimit-limit-tokens" not in h:
        return
    ahora = time.time()
    u = _modelo_uso(modelo)
    u.update(
        tpm_limite=int(h.get("x-ratelimit-limit-tokens", 0)),
        tpm_restante=int(h.get("x-ratelimit-remaining-tokens", 0)),
        tpm_repone=ahora + (_segundos(h.get("x-ratelimit-reset-tokens")) or 0),
        rpd_limite=int(h.get("x-ratelimit-limit-requests", 0)),
        rpd_restante=int(h.get("x-ratelimit-remaining-requests", 0)),
        rpd_repone=ahora + (_segundos(h.get("x-ratelimit-reset-requests")) or 0),
        actualizado=ahora,
    )


def _sumar(modelo, tokens, costo=0.0):
    u = _modelo_uso(modelo)
    u["tokens_hoy"] += int(tokens or 0)
    u["consultas_hoy"] += 1
    u["costo_hoy"] += costo


def _sumar_claude(modelo, usage):
    if not usage:
        return
    entrada = (usage.input_tokens or 0) + 1.25 * (getattr(usage, "cache_creation_input_tokens", 0) or 0) \
        + 0.1 * (getattr(usage, "cache_read_input_tokens", 0) or 0)
    pe, ps = PRECIOS.get(modelo, (4.0, 20.0))
    total = (usage.input_tokens or 0) + (usage.output_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) \
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    _sumar(modelo, total, (entrada * pe + (usage.output_tokens or 0) * ps) / 1e6)


def estado_uso():
    """Resumen para la interfaz: límites, lo que queda y en cuántos segundos se repone."""
    ahora = time.time()
    modelos = [MODELO_GROQ, MODELO_RESPALDO] if PROVEEDOR == "groq" else [CLAUDE_MODELO, CLAUDE_MODELO_RAPIDO]
    salida = []
    for m in dict.fromkeys(modelos):
        u = _modelo_uso(m)
        d = {"modelo": m, "tokens_hoy": u["tokens_hoy"], "consultas_hoy": u["consultas_hoy"], "costo_hoy": round(u["costo_hoy"], 4)}
        if "tpm_limite" in u:
            # pasado el momento de reposición, el cupo vuelve a estar completo
            d.update(
                tpm_limite=u["tpm_limite"],
                tpm_restante=u["tpm_limite"] if ahora >= u["tpm_repone"] else u["tpm_restante"],
                tpm_repone_en=max(0, round(u["tpm_repone"] - ahora, 1)),
                rpd_limite=u["rpd_limite"], rpd_restante=u["rpd_restante"],
                rpd_repone_en=max(0, round(u["rpd_repone"] - ahora)),
                hace=round(ahora - u["actualizado"]),
            )
        salida.append(d)
    return {"proveedor": PROVEEDOR, "limite_tokens_dia": LIMITE_TOKENS_DIA if PROVEEDOR == "groq" else None, "modelos": salida}


async def sondear_limites():
    """Si aún no hay datos de Groq, hace una consulta mínima (1 token) para leer los límites."""
    if PROVEEDOR != "groq":
        return
    for m in dict.fromkeys([MODELO_GROQ, MODELO_RESPALDO]):
        if "tpm_limite" in _modelo_uso(m):
            continue
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.post(f"{BASE_URL}/chat/completions", headers=_cabeceras(),
                                 json={"model": m, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1})
            _registrar_cabeceras(m, r.headers)
        except httpx.HTTPError:
            pass


class ErrorLLM(Exception):
    pass


def _sin_clave():
    raise ErrorLLM("Falta la clave del modelo: pon ANTHROPIC_API_KEY (o GROQ_API_KEY) en el archivo .env.")


# =============================== Claude =======================================

def _separar_system(mensajes):
    system = "\n\n".join(m["content"] for m in mensajes if m["role"] == "system")
    resto = [m for m in mensajes if m["role"] != "system"]
    return system, resto


async def _claude_stream(mensajes, esfuerzo, max_tokens):
    system, resto = _separar_system(mensajes)
    try:
        async with _claude.beta.messages.stream(
            model=CLAUDE_MODELO,
            max_tokens=max_tokens,
            # el prompt de sistema es fijo: se cachea y abarata las preguntas siguientes
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=resto,
            output_config={"effort": esfuerzo},
            betas=_BETAS,
            fallbacks="default",
        ) as stream:
            async for texto in stream.text_stream:
                yield texto
            final = await stream.get_final_message()
            _sumar_claude(CLAUDE_MODELO, final.usage)
            if final.stop_reason == "refusal":
                yield "\n\n_No puedo responder esa solicitud._"
    except anthropic.RateLimitError:
        raise ErrorLLM("Hay muchas consultas en este momento. Intenta de nuevo en unos segundos.")
    except anthropic.AuthenticationError:
        raise ErrorLLM("La clave de Claude (ANTHROPIC_API_KEY) no es válida.")
    except anthropic.APIStatusError as e:
        raise ErrorLLM(f"Error del modelo ({e.status_code}).")
    except anthropic.APIConnectionError:
        raise ErrorLLM("No se pudo conectar con Claude. Revisa la conexión.")


async def _claude_rapido(prompt, max_tokens):
    r = await _claude.messages.create(
        model=CLAUDE_MODELO_RAPIDO,
        max_tokens=max_tokens,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt}],
    )
    _sumar_claude(CLAUDE_MODELO_RAPIDO, r.usage)
    return "".join(b.text for b in r.content if b.type == "text")


# =============================== Groq / OpenAI-compatible ====================

def _cuerpo(modelo, mensajes, max_tokens, esfuerzo, stream):
    cuerpo = {"model": modelo, "messages": mensajes, "max_tokens": max_tokens, "temperature": 0.3, "stream": stream}
    if stream:
        cuerpo["stream_options"] = {"include_usage": True}
    if "gpt-oss" in modelo and "groq.com" in BASE_URL:
        cuerpo["reasoning_effort"] = esfuerzo
        cuerpo["include_reasoning"] = False
        cuerpo["temperature"] = 1 if esfuerzo == "high" else 0.6
    return cuerpo


def _cabeceras():
    return {"Authorization": f"Bearer {API_KEY}"}


async def _groq_stream(mensajes, esfuerzo, max_tokens):
    """Groq gratis limita a ~8.000 tokens/minuto: si el modelo grande pide esperar unos segundos,
    se espera y se reintenta; si no, se pasa al modelo de respaldo. Un modelo que no escribe nada
    también cede el turno al siguiente."""
    import asyncio

    ultimo_error = ""
    intentos = [MODELO_GROQ, MODELO_GROQ, MODELO_RESPALDO]
    esperado = False
    for i, modelo in enumerate(intentos):
        if i == 1 and not esperado:
            continue
        produjo, fin, uso_final = False, None, None
        async with httpx.AsyncClient(timeout=httpx.Timeout(150, connect=10)) as c:
            async with c.stream("POST", f"{BASE_URL}/chat/completions", headers=_cabeceras(),
                                json=_cuerpo(modelo, mensajes, max_tokens, esfuerzo, True)) as r:
                _registrar_cabeceras(modelo, r.headers)
                if r.status_code != 200:
                    ultimo_error = f"{r.status_code}: {(await r.aread()).decode(errors='ignore')[:200]}"
                    espera = float(r.headers.get("retry-after", "99") or 99)
                    if r.status_code == 429 and i == 0 and espera <= 20:
                        esperado = True
                        await asyncio.sleep(espera + 0.5)
                    if r.status_code in (413, 429, 500, 502, 503):
                        continue
                    raise ErrorLLM(ultimo_error)
                async for linea in r.aiter_lines():
                    if not linea.startswith("data: "):
                        continue
                    dato = linea[6:]
                    if dato.strip() == "[DONE]":
                        break
                    try:
                        pedazo = json.loads(dato)
                    except json.JSONDecodeError:
                        continue
                    # Groq puede enviar el uso dos veces al final (x_groq y usage): se guarda y se suma una sola vez
                    uso_final = pedazo.get("usage") or (pedazo.get("x_groq") or {}).get("usage") or uso_final
                    if not pedazo.get("choices"):
                        continue
                    eleccion = pedazo["choices"][0]
                    fin = eleccion.get("finish_reason") or fin
                    if eleccion["delta"].get("content"):
                        produjo = True
                        yield eleccion["delta"]["content"]
        if uso_final:
            _sumar(modelo, uso_final.get("total_tokens"))
        if produjo:
            if fin == "length":
                yield "\n\n_(La respuesta se cortó por el límite del modelo gratuito; pide «continúa» para seguir.)_"
            return
    raise ErrorLLM("El modelo gratuito llegó a su límite por minuto. Espera unos segundos y vuelve a intentar.")


# =============================== API pública ==================================

async def transmitir(mensajes, esfuerzo="low", max_tokens=1500):
    """Genera la respuesta en fragmentos de texto. esfuerzo: low | medium | high."""
    if PROVEEDOR == "claude":
        async for t in _claude_stream(mensajes, esfuerzo, max_tokens):
            yield t
    elif PROVEEDOR == "groq":
        async for t in _groq_stream(mensajes, "high" if esfuerzo == "high" else ("medium" if esfuerzo == "medium" else "low"), max_tokens):
            yield t
    else:
        _sin_clave()


async def completar_rapido(prompt, max_tokens=600, timeout=12):
    """Llamada corta con el modelo rápido (Claude Haiku / gpt-oss-20b). Devuelve '' si falla."""
    try:
        if PROVEEDOR == "claude":
            import asyncio
            return await asyncio.wait_for(_claude_rapido(prompt, max_tokens), timeout)
        if PROVEEDOR == "groq":
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(f"{BASE_URL}/chat/completions", headers=_cabeceras(),
                                 json=_cuerpo(MODELO_TRADUCTOR, [{"role": "user", "content": prompt}], max_tokens + 400, "low", False))
            _registrar_cabeceras(MODELO_TRADUCTOR, r.headers)
            if r.status_code == 200:
                _sumar(MODELO_TRADUCTOR, (r.json().get("usage") or {}).get("total_tokens"))
                return r.json()["choices"][0]["message"]["content"] or ""
    except Exception:
        pass
    return ""


PROMPT_TRADUCTOR = (
    "Convert this question (usually Spanish) into English search keywords for academic papers on quality "
    "management in manufacturing. Output ONLY 5-15 keywords/short phrases separated by commas.\n\nQuestion: "
)


async def traducir_consulta(pregunta):
    """Términos de búsqueda en inglés para la pregunta. Si falla, devuelve '' (queda el glosario)."""
    return await completar_rapido(PROMPT_TRADUCTOR + pregunta, 120, timeout=6)


PROMPT_ANALISIS = """Eres el planificador de Sigma, un asistente de gestión de calidad en manufactura (ciclo PHVA).
Interpreta el último mensaje del usuario DENTRO de la conversación y devuelve SOLO un JSON válido, sin texto extra:
{
 "tipo": "consulta | plan | conversacion | fuera_de_tema | sin_sentido",
 "consulta_independiente": "el mensaje reescrito en español para entenderse solo, resolviendo 'eso', 'lo anterior', 'y en mi caso', etc.",
 "aclaraciones": ["solo si tipo=plan y falta información indispensable: 1 a 3 preguntas cortas"],
 "perfil": {"sector": "", "producto": "", "problemas": [], "objetivos": [], "tamano": ""},
 "extension": "corta | media | larga",
 "formato": "conversacion | lista | pasos | fases | checklist | cronograma | metricas | tabla | informe | documento",
 "formato_pedido": false,
 "fases": ["fases PHVA relacionadas"],
 "consultas_en": ["3 a 5 consultas académicas en inglés, CORTAS (3 a 6 palabras clave), distintas entre sí, p. ej. ISO 9001 implementation manufacturing SME"],
 "subpreguntas": ["2 a 4 subpreguntas en español que la respuesta debe cubrir"]
}

Cómo elegir "tipo":
- sin_sentido: texto al azar, teclazos o palabras sueltas sin relación que no se pueden interpretar ni con el contexto ("asdasd", "mesa azul correr", "qwerty"). Ante la duda real entre sin_sentido y consulta, usa sin_sentido solo si de verdad no se entiende qué quiere.
- conversacion: saludos, gracias, "ok", "quién eres", "qué puedes hacer", comentarios breves.
- fuera_de_tema: se entiende, pero no tiene que ver con calidad, manufactura, operaciones o gestión (recetas, fútbol, tareas de otras materias).
- plan: pide ayuda, un plan o qué hacer en su empresa o proceso.
- consulta: pregunta sobre un tema, concepto, artículo o herramienta. Un término del área también cuenta ("kaizen", "iso 9001"), igual que un mensaje con errores de ortografía o una continuación corta que se entiende con el contexto ("¿y eso cómo se mide?", "dame un ejemplo").

"extension": mide cuánto pide de verdad. corta = dato puntual, definición o "en una frase" (1 a 3 frases); media = explicación normal; larga = plan, análisis o "explícame a fondo".
"formato": la forma que mejor sirve a ESTE pedido. conversacion = texto corrido; lista = puntos sueltos; pasos = procedimiento ordenado; fases = organizado por Planificar, Hacer, Verificar, Actuar; checklist = lista para ir marcando; cronograma = actividades en el tiempo; metricas = indicadores y metas; tabla = solo si compara cosas o la piden; informe = secciones con subtítulos; documento = pide crear un entregable listo para usar (procedimiento, instructivo, informe, política, acta). "formato_pedido": true solo si el usuario pidió esa forma con palabras ("en un cuadro", "dame una lista").

"aclaraciones": déjalo vacío salvo que sea tipo plan y, sin ese dato, el plan sería genérico (por ejemplo "ayúdame con mi empresa", sin decir qué fabrica ni qué problema tiene). Si ya dijo el sector o producto y el problema u objetivo, déjalo vacío. Déjalo vacío también si en la conversación ya respondió aclaraciones o dijo que no sabe o que siga.

Perfil: conserva los datos del perfil previo y agrega solo lo que el usuario dijo de SU empresa. No inventes datos; deja vacío lo que no se sepa. Con sin_sentido, conversacion o fuera_de_tema, consultas_en y subpreguntas van vacías.

Perfil previo: {perfil}
Conversación reciente: {historial}
Último mensaje: {pregunta}"""


async def analizar(pregunta, perfil=None, historial=""):
    """Paso de razonamiento previo: entiende el caso y planifica la búsqueda."""
    texto = await completar_rapido(
        PROMPT_ANALISIS.replace("{perfil}", json.dumps(perfil or {}, ensure_ascii=False))
        .replace("{historial}", historial[-1500:] or "(ninguna)")
        .replace("{pregunta}", pregunta),
        700, timeout=15)
    ini, fin = texto.find("{"), texto.rfind("}")
    if ini >= 0 and fin > ini:
        try:
            return json.loads(texto[ini:fin + 1])
        except json.JSONDecodeError:
            pass
    return {}


def completar(prompt, max_tokens=800):
    """Llamada simple y síncrona (para scripts, p. ej. resúmenes del índice)."""
    if PROVEEDOR == "claude":
        import asyncio
        return asyncio.run(_claude_rapido(prompt, max_tokens))
    if PROVEEDOR == "groq":
        for m in [MODELO_GROQ, MODELO_RESPALDO]:
            r = httpx.post(f"{BASE_URL}/chat/completions", headers=_cabeceras(),
                           json=_cuerpo(m, [{"role": "user", "content": prompt}], max_tokens, "low", False), timeout=90)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"] or ""
            if r.status_code not in (429, 500, 502, 503):
                raise ErrorLLM(f"{r.status_code}: {r.text[:200]}")
        raise ErrorLLM("modelos saturados")
    _sin_clave()
