"""
TrueNoise — Backend API
Receives session uploads from the iOS app and serves aggregated data to the dashboard.
Deploy on Render, database on Supabase (PostgreSQL).
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import csv
import io
import os
import secrets
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timezone
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

# ---------------------------------------------------------------------------
# API key authentication — upload endpoint only
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("TRUENOISE_API_KEY")

def verify_api_key(x_api_key: str = Header(None)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API key not configured on server")
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key

# ---------------------------------------------------------------------------
# Rate limiting — 10 uploads per IP per hour
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="TrueNoise API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://truenoise.org",
        "https://www.truenoise.org",
        "https://mdkriza-cpu.github.io",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    conn = psycopg.connect(DATABASE_URL + "?sslmode=require", row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = psycopg.connect(DATABASE_URL + "?sslmode=require")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id SERIAL PRIMARY KEY,
            session_id TEXT NOT NULL,
            timestamp TEXT,
            type TEXT,
            dba_level REAL,
            loudness_sone REAL,
            loudness_health_impact TEXT,
            loudness_level_phon REAL,
            loudness_context TEXT,
            sharpness_acum REAL,
            sharpness_health_impact TEXT,
            annoyance REAL,
            annoyance_health_impact TEXT,
            onset_rate REAL,
            onset_health_impact TEXT,
            callsign TEXT,
            icao24 TEXT,
            type_code TEXT,
            type_name TEXT,
            registration TEXT,
            operator TEXT,
            flight_phase TEXT,
            ground_distance_mi REAL,
            slant_range_mi REAL,
            altitude_ft REAL,
            bearing REAL,
            bearing_compass TEXT,
            elevation_angle REAL,
            speed_kts REAL,
            climb_rate_fpm REAL,
            approaching TEXT,
            observer_lat REAL,
            observer_lon REAL,
            uploaded_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            observer_lat REAL,
            observer_lon REAL,
            session_start TEXT,
            session_end TEXT,
            total_observations INTEGER,
            unique_aircraft INTEGER,
            n65 INTEGER,
            n70 INTEGER,
            n80 INTEGER,
            event_density REAL,
            recovery_deficit INTEGER,
            peak_dba REAL,
            peak_loudness_sone REAL,
            peak_annoyance REAL,
            who_daily_average_dba REAL,
            who_exceedance_pct REAL,
            uploaded_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()


@app.get("/")
def root():
    return {"status": "TrueNoise API is running"}


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/v1/upload-session")
@limiter.limit("10/hour")
async def upload_session(
    request: Request,
    file: UploadFile = File(...),
    db = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files accepted")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=",")

    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV is empty")

    first_row = rows[0]
    session_id = (
        first_row.get("Timestamp", "").replace(" ", "_").replace(":", "-")
        + "_"
        + str(first_row.get("Observer Lat", "0"))
    )
    uploaded_at = datetime.now(timezone.utc).isoformat()

    cursor = db.cursor()
    inserted = 0
    dba_values = []
    loudness_values = []
    annoyance_values = []
    aircraft_seen = set()

    for row in rows:
        try:
            dba = float(row.get("dBA Level") or 0)
            loudness = float(row.get("Loudness (sone)") or 0)
            annoyance = float(row.get("Annoyance") or 0)
            callsign = (row.get("Callsign") or "").strip()

            dba_values.append(dba)
            loudness_values.append(loudness)
            annoyance_values.append(annoyance)
            if callsign:
                aircraft_seen.add(callsign)

            cursor.execute("""
                INSERT INTO observations (
                    session_id, timestamp, type, dba_level,
                    loudness_sone, loudness_health_impact,
                    loudness_level_phon, loudness_context,
                    sharpness_acum, sharpness_health_impact,
                    annoyance, annoyance_health_impact,
                    onset_rate, onset_health_impact,
                    callsign, icao24, type_code, type_name,
                    registration, operator, flight_phase,
                    ground_distance_mi, slant_range_mi, altitude_ft,
                    bearing, bearing_compass, elevation_angle,
                    speed_kts, climb_rate_fpm, approaching,
                    observer_lat, observer_lon, uploaded_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (session_id, timestamp, callsign) DO NOTHING
            """, (
                session_id,
                row.get("Timestamp"),
                row.get("Type"),
                dba,
                loudness,
                row.get("Loudness Health Impact"),
                _float(row.get("Loudness Level (phon)")),
                row.get("Loudness Context"),
                _float(row.get("Sharpness (acum)")),
                row.get("Sharpness Health Impact"),
                annoyance,
                row.get("Annoyance Health Impact"),
                _float(row.get("Onset Rate (dB/s)")),
                row.get("Onset Health Impact"),
                callsign,
                row.get("ICAO24"),
                row.get("Type Code"),
                row.get("Type Name"),
                row.get("Registration"),
                row.get("Operator"),
                row.get("Flight Phase"),
                _float(row.get("Ground Distance (mi)")),
                _float(row.get("Slant Range (mi)")),
                _float(row.get("Altitude (ft)")),
                _float(row.get("Bearing")),
                row.get("Bearing Compass"),
                _float(row.get("Elevation Angle")),
                _float(row.get("Speed (kts)")),
                _float(row.get("Climb Rate (fpm)")),
                row.get("Approaching"),
                _float(row.get("Observer Lat")),
                _float(row.get("Observer Lon")),
                uploaded_at,
            ))
            inserted += cursor.rowcount
        except Exception:
            continue

    timestamps = [r.get("Timestamp", "") for r in rows if r.get("Timestamp")]
    session_start = min(timestamps) if timestamps else ""
    session_end = max(timestamps) if timestamps else ""

    aircraft_peaks: dict = {}
    for row in rows:
        cs = (row.get("Callsign") or "").strip()
        dba = _float(row.get("dBA Level"))
        if cs and dba is not None:
            aircraft_peaks[cs] = max(aircraft_peaks.get(cs, 0), dba)

    n65 = sum(1 for v in aircraft_peaks.values() if v >= 65)
    n70 = sum(1 for v in aircraft_peaks.values() if v >= 70)
    n80 = sum(1 for v in aircraft_peaks.values() if v >= 80)

    entry_times = sorted([
        r.get("Timestamp", "") for r in rows
        if r.get("Type") == "entry" and r.get("Timestamp")
    ])
    recovery_deficit = 0
    for i in range(1, len(entry_times)):
        try:
            t1 = datetime.fromisoformat(entry_times[i-1])
            t2 = datetime.fromisoformat(entry_times[i])
            if (t2 - t1).total_seconds() / 60 < 15:
                recovery_deficit += 1
        except Exception:
            pass

    try:
        t_start = datetime.fromisoformat(session_start)
        t_end = datetime.fromisoformat(session_end)
        duration_hr = max((t_end - t_start).total_seconds() / 3600, 0.01)
        event_density = round(len(entry_times) / duration_hr, 1)
    except Exception:
        event_density = 0.0

    cursor.execute("""
        INSERT INTO sessions (
            session_id, observer_lat, observer_lon,
            session_start, session_end,
            total_observations, unique_aircraft,
            n65, n70, n80,
            event_density, recovery_deficit,
            peak_dba, peak_loudness_sone, peak_annoyance,
            uploaded_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (session_id) DO UPDATE SET
            total_observations = EXCLUDED.total_observations,
            unique_aircraft = EXCLUDED.unique_aircraft,
            n65 = EXCLUDED.n65,
            n70 = EXCLUDED.n70,
            n80 = EXCLUDED.n80,
            event_density = EXCLUDED.event_density,
            recovery_deficit = EXCLUDED.recovery_deficit,
            peak_dba = EXCLUDED.peak_dba,
            peak_loudness_sone = EXCLUDED.peak_loudness_sone,
            peak_annoyance = EXCLUDED.peak_annoyance,
            uploaded_at = EXCLUDED.uploaded_at
    """, (
        session_id,
        _float(first_row.get("Observer Lat")),
        _float(first_row.get("Observer Lon")),
        session_start, session_end,
        inserted, len(aircraft_seen),
        n65, n70, n80,
        event_density, recovery_deficit,
        max(dba_values) if dba_values else 0,
        max(loudness_values) if loudness_values else 0,
        max(annoyance_values) if annoyance_values else 0,
        uploaded_at,
    ))

    db.commit()

    return {
        "status": "ok",
        "session_id": session_id,
        "observations_inserted": inserted,
        "n65": n65, "n70": n70, "n80": n80,
        "recovery_deficit": recovery_deficit,
        "unique_aircraft": len(aircraft_seen),
    }


@app.get("/api/v1/dashboard-summary")
def dashboard_summary(db = Depends(get_db)):
    cursor = db.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as total_sessions,
            SUM(total_observations) as total_observations,
            SUM(unique_aircraft) as total_aircraft,
            SUM(n65) as total_n65,
            SUM(n70) as total_n70,
            SUM(n80) as total_n80,
            SUM(recovery_deficit) as total_recovery_deficit,
            MAX(peak_dba) as all_time_peak_dba,
            MAX(peak_loudness_sone) as all_time_peak_sone,
            AVG(event_density) as avg_event_density
        FROM sessions
    """)
    totals = dict(cursor.fetchone() or {})

    cursor.execute("""
        SELECT session_start, n70, recovery_deficit, peak_dba, event_density, uploaded_at
        FROM sessions
        ORDER BY uploaded_at DESC
        LIMIT 30
    """)
    recent = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT type_name,
               flight_phase,
               COUNT(DISTINCT session_id) as events,
               MAX(dba_level) as peak_dba,
               AVG(loudness_sone) as avg_loudness,
               AVG(sharpness_acum) as avg_sharpness,
               AVG(annoyance) as avg_annoyance,
               AVG(altitude_ft) as avg_altitude_ft
        FROM observations
        WHERE type_name IS NOT NULL AND type_name != ''
        AND flight_phase IS NOT NULL AND flight_phase != ''
        GROUP BY type_name, flight_phase
        ORDER BY peak_dba DESC
        LIMIT 20
    """)
    aircraft_breakdown = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT
            CAST(SUBSTRING(timestamp, 12, 2) AS INTEGER) as hour,
            COUNT(*) as events,
            AVG(dba_level) as avg_dba
        FROM observations
        WHERE type = 'entry'
        GROUP BY hour
        ORDER BY hour
    """)
    hourly = [dict(r) for r in cursor.fetchall()]

    return {
        "totals": totals,
        "recent_sessions": recent,
        "aircraft_breakdown": aircraft_breakdown,
        "hourly_distribution": hourly,
    }


@app.get("/api/v1/sessions")
def list_sessions(limit: int = 50, db = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("""
        SELECT * FROM sessions
        ORDER BY session_start DESC
        LIMIT %s
    """, (limit,))
    return [dict(r) for r in cursor.fetchall()]


@app.get("/api/v1/sessions/{session_id}/observations")
def session_observations(session_id: str, db = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("""
        SELECT * FROM observations
        WHERE session_id = %s
        ORDER BY timestamp
    """, (session_id,))
    rows = cursor.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Session not found")
    return [dict(r) for r in rows]


def _float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
