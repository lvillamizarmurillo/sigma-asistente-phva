# Punto de entrada para Vercel (función serverless de Python).
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app as _app  # noqa: E402


class RutaOriginal:
    """vercel.json manda todo a /api/index?_ruta=...: aquí se restaura la ruta original."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            query = parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
            ruta = next((v for k, v in query if k == "_ruta"), None)
            if ruta is not None or scope["path"] == "/api/index":
                scope = dict(scope)
                scope["path"] = "/" + (ruta or "").lstrip("/")
                scope["raw_path"] = scope["path"].encode()
                scope["query_string"] = urlencode([(k, v) for k, v in query if k != "_ruta"]).encode()
        await _app(scope, receive, send)


app = RutaOriginal()
