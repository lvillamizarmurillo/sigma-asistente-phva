# Sigma · Asistente de gestión de calidad (ciclo PHVA)

IA conversacional que responde **en español** sobre gestión de calidad en la industria manufacturera y **propone soluciones** por fase PHVA para el caso concreto de una empresa. Se basa en 50 artículos científicos seleccionados por el proyecto, 40 complementarios de acceso abierto, un catálogo de soluciones y fuentes académicas reales consultadas en línea.

## Capas de conocimiento

| Capa | Archivo | Qué aporta |
|---|---|---|
| 50 artículos del proyecto | `datos/indice.json` | Texto completo (24) o resumen; **barrera, conclusión y solución** de las Tablas 1 y 2 del proyecto; resumen en español |
| 40 artículos complementarios | `datos/complementarios.json` | ISO 9001, estandarización, SOP, TPM, SPC, metalmecánica (acceso abierto, texto completo) |
| Catálogo de soluciones | `datos/soluciones.json` | 12 problemas típicos → acciones por fase PHVA, herramientas, indicadores, cláusulas ISO 9001 y artículos de respaldo; mapa ISO 9001:2015 y ruta de certificación; perfil del sector metalmecánico |
| Fuentes en línea | OpenAlex (en vivo) | Artículos reales y recientes con DOI, citados como [W1], [W2]… |
| Glosario | `datos/glosario.json` | Español → inglés para buscar en artículos en inglés |

## Cómo razona (modos)

| Modo | Qué hace |
|---|---|
| ⚡ **Rápido** | Traduce la pregunta, busca (BM25 + glosario + temas) y responde breve con citas |
| ✦ **Profundo** | 1) **Analiza** la pregunta y el caso (perfil de empresa, subpreguntas, consultas en inglés) → 2) **busca con varias consultas** → 3) suma **soluciones PHVA** del catálogo → 4) **fuentes en línea** si se activan → 5) razona a fondo y responde |
| 🧭 **Consultor** | Igual que Profundo, siempre con fuentes en línea, y entrega: **Diagnóstico → Tabla de solución PHVA (con respaldo) → Ruta ISO 9001 → Indicadores → Primeros 30 días** |

- Los pasos del razonamiento se ven en el chat («Analizando tu caso…», «Buscando en 90 artículos…», etc.).
- **Memoria del caso** durante la conversación: Sigma recuerda el perfil de tu empresa (sector, problemas y objetivos) y las últimas 5 preguntas con sus respuestas; todo se borra al recargar la página.
- Cada respuesta trae preguntas sugeridas por la IA, botones «✦ Profundizar» y «Plan PHVA para mi empresa», copiar, escuchar y **descargar en Word**.

## Modelo: Claude o Groq (automático)

| Si en `.env` hay… | Se usa |
|---|---|
| `ANTHROPIC_API_KEY=sk-ant-...` | **Claude**: `claude-opus-5-5` responde y `claude-haiku-5-5` analiza y traduce |
| solo `GROQ_API_KEY=gsk_...` | **Groq gratis**: `openai/gpt-oss-120b`, con `openai/gpt-oss-20b` de respaldo |

Groq gratis permite unos 8.000 tokens por minuto, así que los modos Profundo y Consultor usan menos contexto; si se llega al límite, Sigma espera unos segundos o pasa al modelo de respaldo. Con Claude el contexto es entre 3 y 4 veces mayor y las respuestas son más completas. Al comprar la API, pega la clave en `ANTHROPIC_API_KEY=` y reinicia el servidor.

## Correr en local

```bash
pip install -r requirements-scripts.txt
```

```bash
python -m uvicorn app.main:app --port 8000
```

Abre http://localhost:8000

## Regenerar la base de conocimiento

```bash
python scripts/descargar.py
```

```bash
python scripts/importar_tablas.py
```

```bash
python scripts/ampliar_corpus.py --max 40
```

```bash
python scripts/construir_indice.py --resumir
```

`descargar.py` obtiene los 50 artículos del Excel; `repositorios.py` hace un segundo intento en repositorios universitarios; `importar_tablas.py` lee las Tablas 1 y 2 (Word); `ampliar_corpus.py` agrega complementarios de acceso abierto (`--limpiar` quita los que no son pertinentes); `construir_indice.py` arma el índice y, con `--resumir`, genera los resúmenes en español. Los PDFs que consigas a mano se guardan como `documentos/NN.pdf` (ver `PDFS_FALTANTES.md`).

## Desplegar (gratis)

**Vercel:** sube el proyecto a un repositorio **privado** de GitHub (los PDFs están excluidos), impórtalo en vercel.com y agrega `ANTHROPIC_API_KEY` (o `GROQ_API_KEY`) en *Settings → Environment Variables*. `vercel.json` ya apunta todo a `api/index.py`.

**Railway / Render:** mismo repositorio; el comando de arranque está en el `Procfile`.

## Costo aproximado con Claude Opus 5.5

Rápido ≈ 0,03 USD · Profundo ≈ 0,10 USD · Consultor ≈ 0,15 USD por consulta. El prompt de sistema se cachea. Para gastar menos: `CLAUDE_MODELO=claude-sonnet-5-5`.
