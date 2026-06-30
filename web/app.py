from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database.database_manage import _get_pool, get_connection
from web.routers import auth, crossrefs, documents, graph, search, settings, synthesize

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"


def _ensure_users_table():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                username VARCHAR(100) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                llm_model VARCHAR(255),
                llm_base_url VARCHAR(500),
                llm_api_key_encrypted TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _get_pool()
    _ensure_users_table()
    yield


app = FastAPI(
    title="FrenchAdmin GraphRAG",
    description="GraphRAG query interface for French legal documents",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(crossrefs.router, prefix="/api")
app.include_router(synthesize.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        file_path = (FRONTEND_DIR / path).resolve()
        if file_path.is_relative_to(FRONTEND_DIR.resolve()) and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
