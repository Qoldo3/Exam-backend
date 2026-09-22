from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from workers import asgi

app = FastAPI(title="exam-api")


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    env = request.scope["env"]
    try:
        result = await env.DB.prepare(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).all()
        rows = result.get("results") if isinstance(result, dict) else getattr(result, "results", result)
        tables = [r["name"] if isinstance(r, dict) else r.name for r in rows]
        tables = [t for t in tables if not t.startswith("_cf_")]
        return JSONResponse({"status": "ok", "db": "d1", "tables": tables})
    except Exception as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)


Default = asgi.entrypoint(app)
