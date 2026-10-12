import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.endpoints import recommendations, providers
from app.config import CORS_ORIGINS
from app.services.anthropic_service import get_anthropic_service
from app.services.supabase_service import get_supabase_service
from app.services.tmdb_service import get_tmdb_service

RESET = "\033[0m"
LEVEL_COLORS = {
    logging.DEBUG:    "\033[36m",    # cyan
    logging.INFO:     "\033[32m",    # green
    logging.WARNING:  "\033[33m",    # yellow
    logging.ERROR:    "\033[31m",    # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}

class ColorFormatter(logging.Formatter):
    def format(self, record):
        color = LEVEL_COLORS.get(record.levelno, RESET)
        record.levelname = f"{color}{record.levelname}{RESET}"
        record.name = f"\033[35m{record.name}{RESET}"  # magenta for logger name
        return super().format(record)

handler = logging.StreamHandler()
handler.setFormatter(ColorFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler])

@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # Services are shared for the app's lifetime; close their connection pools on shutdown.
    for service in (get_anthropic_service(), get_tmdb_service(), get_supabase_service()):
        await service.close()


app = FastAPI(
    title="PikFlix API",
    description="A FastAPI server for handling content recommendations",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(recommendations.router, prefix="/api/recommendations", tags=["recommendations"])
app.include_router(providers.router, prefix="/api/providers", tags=["providers"])

@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)