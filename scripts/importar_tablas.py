"""
Importa las tablas del proyecto (Word) a datos/tablas_phva.json:
  - Tabla 1 (Objetivo 1): barrera y conclusión de cada uno de los 50 artículos.
  - Tabla 2 (Objetivo 2): soluciones propuestas por fase PHVA.

Uso:  python scripts/importar_tablas.py   (luego: python scripts/construir_indice.py)
"""
import json
import re
from pathlib import Path

import docx

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "datos" / "tablas_phva.json"


def buscar(patron):
    archivos = sorted(RAIZ.glob(patron))
    return archivos[-1] if archivos else None


def filas(ruta):
    tabla = docx.Document(str(ruta)).tables[0]
    datos = [[c.text.strip() for c in r.cells] for r in tabla.rows]
    return datos[0], datos[1:]


def numero(texto):
    m = re.match(r"\s*Art\.?\s*(\d+)", texto)
    return int(m.group(1)) if m else None


def main():
    resultado = {}
    t1 = buscar("Tabla_PHVA_50_articulos*.docx")
    if t1:
        _, cuerpo = filas(t1)
        for fase, barrera, art, anio, fuente, conclusion in cuerpo:
            n = numero(art)
            if n:
                resultado.setdefault(n, {}).update({"fase": fase, "barrera": barrera, "conclusion": conclusion})
        print(f"Tabla 1: {len(cuerpo)} filas ({t1.name})")
    t2 = buscar("Tabla_Objetivo2_PHVA_soluciones*.docx")
    if t2:
        _, cuerpo = filas(t2)
        for fase, problema, art, solucion in cuerpo:
            n = numero(art)
            if n:
                resultado.setdefault(n, {}).update({"problema_tabla2": problema, "solucion": solucion})
        print(f"Tabla 2: {len(cuerpo)} filas ({t2.name})")
    SALIDA.write_text(json.dumps({str(k): v for k, v in sorted(resultado.items())}, ensure_ascii=False, indent=2), "utf-8")
    print(f"{len(resultado)} artículos -> {SALIDA}")


if __name__ == "__main__":
    main()
