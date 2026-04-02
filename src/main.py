"""
FIELDHAND — Contractor AI Assistant
FastAPI application entry point.
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pathlib import Path
from src.database import engine, Base
import src.models  # noqa — register all models


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="FIELDHAND",
    description="AI Business Assistant for Solo Trade Contractors",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount generated docs as static files
generated = Path("generated_docs")
generated.mkdir(exist_ok=True)
app.mount("/docs-static", StaticFiles(directory="generated_docs"), name="static")

# Routers
from src.routes.sms import router as sms_router
from src.routes.stripe_webhook import router as stripe_router
from src.routes.dashboard import router as dashboard_router
from src.routes.consent import router as consent_router

app.include_router(sms_router)
app.include_router(stripe_router)
app.include_router(dashboard_router)
app.include_router(consent_router)


@app.get("/status")
def root():
    return {"status": "FIELDHAND is running", "version": "0.1.0"}


@app.get("/health")
def health():
    return {"ok": True}
