"""
Búsqueda sobre los 50 artículos: BM25 sobre fragmentos + refuerzo por temas y
palabras clave del índice JSON + glosario español -> inglés.
Todo en Python puro: carga en milisegundos y no necesita base de datos.
"""
import math
import re
import unicodedata
from collections import Counter, defaultdict

STOP = set("""
a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers him his how i if in into is it its itself just me more most my no nor not now
of off on once only or other our out over own same she should so some such than that the their them
then there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your et al fig table figure study paper research results using
used use based may however thus therefore within among one two three also e g i e vol pp doi
de la el los las un una unos unas y o u en con por para del al que se su sus es son fue ser como mas
pero sin sobre entre este esta estos estas ese esa eso lo le les ya muy tambien cual cuales cuando
donde quien quienes cuanto cuantos cuantas me mi mis tu tus nos hay han ha he qué cómo puedes podrias
dime explica explicame dame quiero saber segun articulos articulo documento documentos hablan habla
""".split())

FASES = {
    "planificar": "Planificar", "planear": "Planificar", "planeacion": "Planificar",
    "hacer": "Hacer", "ejecutar": "Hacer",
    "verificar": "Verificar", "chequear": "Verificar",
    "actuar": "Actuar",
}


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def _raiz(p):
    if len(p) > 4 and p.endswith("ies"):
        return p[:-3] + "y"
    if len(p) > 3 and p.endswith("s") and not p.endswith("ss"):
        return p[:-1]
    return p


def tokenizar(texto, bigramas=True):
    palabras = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", normalizar(texto))
    toks = [_raiz(p) for p in palabras if p not in STOP and len(p) > 1]
    if bigramas:
        toks += [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    return toks


class Buscador:
    def __init__(self, indice, glosario):
        self.articulos = {a["id"]: a for a in indice["articulos"]}
        self.fragmentos = indice["fragmentos"]
        self.glosario = sorted(
            ((normalizar(k), v) for k, v in glosario.items()),
            key=lambda kv: -len(kv[0]),
        )
        self.k1, self.b = 1.4, 0.75
        self.tf = []
        self.largos = []
        df = Counter()
        for f in self.fragmentos:
            c = Counter(tokenizar(f["texto"]))
            self.tf.append(c)
            self.largos.append(sum(c.values()))
            df.update(c.keys())
        n = len(self.fragmentos)
        self.prom = sum(self.largos) / max(n, 1)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        # tokens de temas/palabras clave por artículo, para el refuerzo
        self.etiquetas = {
            a["id"]: set(tokenizar(" ".join(a.get("temas", []) + a.get("palabras_clave", []) + [a["titulo"]])))
            for a in indice["articulos"]
        }

    def expandir(self, pregunta):
        """Agrega equivalentes en inglés de los términos del glosario."""
        texto = " " + re.sub(r"[^a-z0-9. ]", " ", normalizar(pregunta)) + " "
        extra = []
        for es, en in self.glosario:
            if f" {es} " in texto:
                extra += en
                texto = texto.replace(f" {es} ", " ")
        return extra

    def detectar_fase(self, pregunta):
        m = re.search(r"\b(?:fase|etapa)\s+(?:de\s+)?(\w+)", normalizar(pregunta))
        if m and m.group(1) in FASES:
            return FASES[m.group(1)]
        return None

    def buscar(self, consulta, k=6, max_por_articulo=2, fase=None):
        q = Counter(tokenizar(consulta))
        if not q:
            return []
        puntajes = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * self.largos[i] / self.prom)
            for t, qf in q.items():
                f = tf.get(t)
                if f:
                    peso = 1.5 if "_" in t else 1.0
                    s += peso * self.idf[t] * f * (self.k1 + 1) / (f + norm)
            if s <= 0:
                continue
            art = self.fragmentos[i]["articulo"]
            coincide = len(self.etiquetas[art] & q.keys())
            s *= 1 + 0.15 * min(coincide, 4)
            if self.fragmentos[i].get("es_resumen"):
                s *= 1.1
            if fase and self.articulos[art]["fase"] == fase:
                s *= 1.5
            puntajes.append((s, i))
        puntajes.sort(reverse=True)
        elegidos, por_art = [], Counter()
        for s, i in puntajes:
            art = self.fragmentos[i]["articulo"]
            if por_art[art] >= max_por_articulo:
                continue
            por_art[art] += 1
            elegidos.append({**self.fragmentos[i], "puntaje": round(s, 2)})
            if len(elegidos) >= k:
                break
        return elegidos

    def buscar_multi(self, consultas, k=10, max_por_articulo=2, fase=None, priorizar=()):
        """Varias consultas (una por subtema): une resultados y normaliza puntajes por consulta."""
        mejores = {}
        for q in filter(None, consultas):
            res = self.buscar(q, k=25, max_por_articulo=3, fase=fase)
            if not res:
                continue
            tope = res[0]["puntaje"]
            for f in res:
                clave = (f["articulo"], f["n"])
                s = f["puntaje"] / tope
                if f["articulo"] in priorizar:
                    s *= 1.2
                if clave not in mejores or s > mejores[clave]["puntaje"]:
                    mejores[clave] = {**f, "puntaje": round(s, 3)}
        elegidos, por_art = [], Counter()
        for f in sorted(mejores.values(), key=lambda x: -x["puntaje"]):
            if por_art[f["articulo"]] >= max_por_articulo:
                continue
            por_art[f["articulo"]] += 1
            elegidos.append(f)
            if len(elegidos) >= k:
                break
        return elegidos

    def articulos_relevantes(self, consulta, k=8):
        """Ranking por artículo (suma de sus mejores fragmentos)."""
        total = defaultdict(float)
        for f in self.buscar(consulta, k=60, max_por_articulo=3):
            total[f["articulo"]] += f["puntaje"]
        return [self.articulos[a] for a, _ in sorted(total.items(), key=lambda x: -x[1])[:k]]
