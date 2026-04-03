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
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    Base.metadata.create_all(bind=engine)

    # ── Start background scheduler ────────────────────────────────────────────
    scheduler = BackgroundScheduler(timezone="UTC")

    # Proactive pulse — every 2 hours, all contractors
    from src.tasks.pulse import run_pulse_all
    scheduler.add_job(run_pulse_all, IntervalTrigger(hours=2), id="pulse",
                      max_instances=1, misfire_grace_time=300)

    # Morning briefing — 7am UTC (2am CT, reasonable for early risers)
    from src.tasks.monitoring import run_morning_briefings
    scheduler.add_job(run_morning_briefings, CronTrigger(hour=12, minute=0),
                      id="morning_briefing", max_instances=1)

    # EOD summary — 11pm UTC (6pm CT)
    from src.tasks.monitoring import run_eod_summaries
    scheduler.add_job(run_eod_summaries, CronTrigger(hour=23, minute=0),
                      id="eod_summary", max_instances=1)

    # Dunning — twice daily (9am and 3pm UTC)
    from src.tasks.monitoring import run_dunning
    scheduler.add_job(run_dunning, CronTrigger(hour="9,15", minute=0),
                      id="dunning", max_instances=1)

    scheduler.start()
    print("[Scheduler] Started — pulse every 2h, briefing 7am CT, EOD 6pm CT, dunning 2x/day")
    # ─────────────────────────────────────────────────────────────────────────

    yield

    scheduler.shutdown(wait=False)
    print("[Scheduler] Stopped")


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
from src.routes.gmail_oauth import router as gmail_router

app.include_router(sms_router)
app.include_router(stripe_router)
app.include_router(dashboard_router)
app.include_router(consent_router)
app.include_router(gmail_router)


@app.get("/status")
def root():
    return {"status": "FIELDHAND is running", "version": "0.1.0"}


@app.get("/health")
def health():
    return {"ok": True}
