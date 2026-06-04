# TrueNoise — Backend Deployment Guide
## For XC (Xcode Claude) and Marty

---

## CURRENT STATUS (as of June 2026)

Everything below is live. This document is the reference for iOS app
development and backend integration.

- **Public site:** https://truenoise.org
- **API backend:** https://aircraft-noise-tracker-api.onrender.com
- **Database:** Supabase PostgreSQL (permanent, no expiry)
- **GitHub repo:** https://github.com/mdkriza-cpu/truenoise-api

---

## ARCHITECTURE SUMMARY

```
iOS App (TrueNoise)
    │
    │  POST /api/v1/upload-session
    │  (CSV file, multipart/form-data)
    ▼
Render (FastAPI backend — main.py)
    │  Rate limit: 10 uploads/hr per IP
    │  psycopg / PostgreSQL
    ▼
Supabase (permanent PostgreSQL database)
    │
    │  GET /api/v1/dashboard-summary
    ▼
truenoise.org (GitHub Pages — static HTML)
```

---

## API SPECIFICATION

### Upload Endpoint
```
POST https://aircraft-noise-tracker-api.onrender.com/api/v1/upload-session
```

### Request format
```
Content-Type: multipart/form-data
X-Api-Key: imDQ5QWAD0mWvmltaUV-0Wcup8nR3xwSrx_gEDgmegw
Form field name: "file"
File content: session CSV (comma-separated)
```

The API key must be sent as the header `X-Api-Key` on every upload request.

### Rate limit
The backend enforces **10 uploads per hour per IP**. Exceeding this returns
`429 Too Many Requests`. The retry strategy below handles this gracefully.

---

## CSV FORMAT — CANONICAL COLUMN ORDER

The CSV uses **comma delimiters**. Column headers must match exactly
(case-sensitive). All columns from Position 32 onward are nullable for
backward compatibility with sessions recorded before those fields existed.

```
Pos  Column name                    Type      Notes
───  ─────────────────────────────  ────────  ──────────────────────────────────
 1   Timestamp                      datetime  Local time
 2   Type                           text      "entry" or "track"
 3   dBA Level                      float
 4   Loudness (sone)                float     Zwicker ISO 532
 5   Loudness Health Impact         text      Risk tier label
 6   Loudness Level (phon)          float
 7   Loudness Context               text
 8   Sharpness (acum)               float     DIN 45692
 9   Sharpness Health Impact        text
10   Annoyance                      float     Zwicker & Fastl composite
11   Annoyance Health Impact        text
12   Onset Rate (dB/s)              float
13   Onset Health Impact            text
14   Callsign                       text      ADS-B flight identifier
15   ICAO24                         text      Transponder hex code
16   Type Code                      text      ICAO aircraft type (e.g. B38M)
17   Type Name                      text      Full name (e.g. Boeing 737MAX 8)
18   Registration                   text      Tail number
19   Operator                       text      Airline / operator name
20   Flight Phase                   text      Taking Off, On Approach, etc.
21   Ground Distance (mi)           float     Horizontal distance from observer
22   Slant Range (mi)               float     3D distance (accounts for altitude)
23   Altitude (ft)                  float     Barometric altitude
24   Bearing                        float     Compass degrees from observer
25   Bearing Compass                text      Cardinal direction string
26   Elevation Angle                float     Look-up angle from observer
27   Speed (kts)                    float     Ground speed
28   Climb Rate (fpm)               float     Vertical rate (+ = climbing)
29   Approaching                    bool
30   Observer Lat                   float     GPS latitude of measurement point
31   Observer Lon                   float     GPS longitude of measurement point
32   Windshield Config              text      None / Foam / Fur
33   Windshield Correction (dBA)    float     0.0 / 0.7 / 1.8
34   Measurement Type               text      See values below
35   Position Description           text      Free-form; quote-escape if commas
36   C-A Delta (dB)                 float     Wind contamination indicator
37   Excluded                       text      Yes or No (default No)
```

**Windshield Config values and insertion loss:**
```
None  →  0.0 dBA
Foam  →  0.7 dBA   (midpoint of 0.6–0.8 measured range)
Fur   →  1.8 dBA   (used for all outdoor sessions)
```

**Measurement Type values:**
```
Standardized Receptor    ISO 1996 compliant · tripod · ≥1 m from walls
Community Receptor       Lived position (porch, balcony, etc.)
Facade-Level             0.5–2 m from single wall · WHO L_DEN methodology
Field Characterization   Mobile/temporary · strategic location
Hand-held                No fixed mount · indicative only
```

**C-A Delta threshold guidance:**
```
< 15 dB    Clean
15–25 dB   Possible wind contamination — flag for review
≥ 25 dB    Strong wind contamination — exclude from SPL analysis
```
Flags only apply above 55 dBA SPL. Below that threshold, a large C-A
delta reflects ambient bass character, not wind contamination.

**Excluded column behavior:**
Rows with `Excluded=Yes` must be stripped by the iOS upload service
before transmission. The backend should never receive excluded rows
under normal operation. The column is stored as a boolean in the
observations table for defense in depth.

---

## CALIBRATION EPOCH — IMPORTANT FOR XC

The backend automatically tags every uploaded observation with a
`calibration_epoch` value based on the row timestamp:

```
pre_2026_06_01   — sessions recorded before 1 June 2026 20:30:00
post_2026_06_01  — sessions recorded from that point forward
```

**The app does not send this column.** It is a server-side generated
column in the observations table. XC does not need to include it in
the CSV or handle it in any way — the backend derives it automatically
from the Timestamp column.

Context: on 1 June 2026 a +2.9 dB calibration drift was identified in
the prior reference meter (TA657A) and corrected. The epoch tag allows
the dashboard and download page to handle pre- and post-correction data
appropriately. See truenoise.org/methodology.html §7b for full details.

---

## SWIFT IMPLEMENTATION

```swift
func uploadSession(csvURL: URL) async throws {
    let url = URL(string: "https://aircraft-noise-tracker-api.onrender.com/api/v1/upload-session")!
    var request = URLRequest(url: url)
    request.httpMethod = "POST"

    let boundary = UUID().uuidString
    request.setValue("multipart/form-data; boundary=\(boundary)",
                     forHTTPHeaderField: "Content-Type")
    request.setValue("imDQ5QWAD0mWvmltaUV-0Wcup8nR3xwSrx_gEDgmegw",
                     forHTTPHeaderField: "X-Api-Key")

    let csvData = try Data(contentsOf: csvURL)
    var body = Data()
    body.append("--\(boundary)\r\n".data(using: .utf8)!)
    body.append("Content-Disposition: form-data; name=\"file\"; filename=\"session.csv\"\r\n".data(using: .utf8)!)
    body.append("Content-Type: text/csv\r\n\r\n".data(using: .utf8)!)
    body.append(csvData)
    body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
    request.httpBody = body

    let (data, response) = try await URLSession.shared.data(for: request)

    guard let httpResponse = response as? HTTPURLResponse else {
        throw UploadError.invalidResponse
    }

    switch httpResponse.statusCode {
    case 200:
        let result = try JSONDecoder().decode(UploadResponse.self, from: data)
        print("Uploaded: \(result.sessionId), \(result.observationsInserted) observations")
    case 429:
        throw UploadError.rateLimited   // retry after 1 hour
    default:
        throw UploadError.serverError(httpResponse.statusCode)
    }
}

struct UploadResponse: Codable {
    let status: String
    let sessionId: String
    let observationsInserted: Int
    let n65: Int
    let n70: Int
    let n80: Int
    let recoveryDeficit: Int
    let uniqueAircraft: Int

    enum CodingKeys: String, CodingKey {
        case status
        case sessionId = "session_id"
        case observationsInserted = "observations_inserted"
        case n65, n70, n80
        case recoveryDeficit = "recovery_deficit"
        case uniqueAircraft = "unique_aircraft"
    }
}

enum UploadError: Error {
    case invalidResponse
    case rateLimited
    case serverError(Int)
}
```

### Expected success response (200 OK)
```json
{
  "status": "ok",
  "session_id": "2026-05-09_17-12-59_39.11281",
  "observations_inserted": 111,
  "n65": 24,
  "n70": 6,
  "n80": 0,
  "recovery_deficit": 25,
  "unique_aircraft": 29
}
```

### Error responses
```
400   Not a CSV file, or CSV is empty
401   Missing or invalid X-Api-Key header
429   Rate limit exceeded (10 uploads/hr per IP) — retry after 1 hour
500   Server error — retry later
```

---

## RETRY STRATEGY

The Render free tier spins down after 15 minutes of inactivity.
First request after idle can take up to 50 seconds.

```swift
// Store failed upload for retry
func queueFailedUpload(csvURL: URL) {
    var queued = UserDefaults.standard.stringArray(forKey: "pendingUploads") ?? []
    queued.append(csvURL.path)
    UserDefaults.standard.set(queued, forKey: "pendingUploads")
}

// On app launch, retry any queued uploads
func retryPendingUploads() async {
    var queued = UserDefaults.standard.stringArray(forKey: "pendingUploads") ?? []
    var remaining: [String] = []
    for path in queued {
        let url = URL(fileURLWithPath: path)
        do {
            try await uploadSession(csvURL: url)
        } catch UploadError.rateLimited {
            remaining.append(path)  // keep — retry after rate limit window
        } catch {
            remaining.append(path)  // keep for next retry
        }
    }
    UserDefaults.standard.set(remaining, forKey: "pendingUploads")
}
```

The backend uses a unique index on `(session_id, timestamp, callsign)`
so duplicate uploads are safe — re-uploading the same session is idempotent.

---

## OTHER API ENDPOINTS

```
GET  /health
     → {"status": "ok", "timestamp": "2026-06-04T..."}

GET  /api/v1/dashboard-summary
     → aggregated stats across all sessions (powers truenoise.org dashboard)

GET  /api/v1/sessions
     → most recent 50 sessions

GET  /api/v1/sessions/{session_id}/observations
     → all raw observations for a specific session
```

---

## DATABASE SCHEMA

### `observations` table
One row per measurement row in the CSV.

```
session_id               text
timestamp                datetime
type                     text        "entry" or "track"
dba_level                real
loudness_sone            real
loudness_health_impact   text
loudness_level_phon      real
loudness_context         text
sharpness_acum           real
sharpness_health_impact  text
annoyance                real
annoyance_health_impact  text
onset_rate               real
onset_health_impact      text
callsign                 text
icao24                   text
type_code                text
type_name                text
registration             text
operator                 text
flight_phase             text
ground_distance_mi       real
slant_range_mi           real
altitude_ft              real
bearing                  real
bearing_compass          text
elevation_angle          real
speed_kts                real
climb_rate_fpm           real
approaching              boolean
observer_lat             real
observer_lon             real
windshield_config        text        nullable
windshield_correction_dba real       nullable
measurement_type         text        nullable
position_description     text        nullable
ca_delta_db              real        nullable
excluded                 boolean     default false
calibration_epoch        text        GENERATED — do not send in CSV
```

Unique index: `(session_id, timestamp, callsign)` — prevents duplicates.
RLS enabled. GRANT SELECT to anon role active.

### `sessions` table
One row per uploaded session with pre-computed summary stats.

```
session_id               text        primary key
session_start            datetime
uploaded_at              datetime
total_observations       integer
n65                      integer
n70                      integer
n80                      integer
recovery_deficit         integer
event_density            real
peak_dba                 real
peak_loudness_sone       real
unique_aircraft          integer
measurement_type         text        nullable
position_description     text        nullable
ca_delta_db              real        nullable
who_daily_average_dba    real        nullable  (future — not yet ingested)
who_exceedance_pct       real        nullable  (future — not yet ingested)
laeq                     real        nullable  (future — not yet ingested)
la10                     real        nullable  (future — not yet ingested)
la50                     real        nullable  (future — not yet ingested)
la90                     real        nullable  (future — not yet ingested)
la95                     real        nullable  (future — not yet ingested)
baseline_sample_count    integer     nullable  (future — not yet ingested)
baseline_duration_seconds real       nullable  (future — not yet ingested)
```

---

## BACKGROUND BASELINE — NOT YET INGESTED

The iOS app computes LAeq, LA10, LA50, LA90, LA95 per session. The upload
service currently strips this block before sending to the backend. Schema
columns exist for future use.

**Do not display baseline stats on the dashboard** until Marty sends a
separate "go" note with validated data and methodology text.

Baseline block format (local only, not uploaded):
```
BACKGROUND BASELINE (from 2-sec peak samples)
Sample count,<int>
Sampling duration,<hh:mm or mm:ss>
LAeq (approx),<float>
LA10 (exceeded 10% of time),<float>
LA50 (median),<float>
LA90 (background floor),<float>
LA95 (acoustic floor),<float>
```

---

## NOTES

**Naming:** The Render service is still named `aircraft-noise-tracker-api`
(legacy). It functions correctly. A full rename is not a priority.

**Two public sites:**
- **truenoise.org** — data, methodology, dashboard
- **stopsevernnoise.org** — community advocacy (in planning, not yet built)

**Calibration reference (current):**
App external mic offset: **+96 dB** (corrected from +99 dB on 1 June 2026).
Internal mic offset: +108 dB (not yet re-validated post-correction — not
used for field measurement). See methodology.html for full calibration chain.

---

## CHANGELOG

```
2026-06-04  Consolidated all append blocks into single canonical document.
            Added calibration_epoch documentation (server-generated, app
            does not send). Added rate limit (10/hr) to architecture,
            error responses, and retry strategy. Added 429 error case to
            Swift implementation. Updated status header to June 2026.
            Full observations schema table with all current columns.

2026-05-21  Excluded column added to CSV (pos 37). Rows with Excluded=Yes
            stripped by iOS before upload. Stored as boolean in observations.

2026-05-18  Measurement Type, Position Description, C-A Delta columns added
            (pos 34–36). Background baseline schema columns added to sessions
            table (not yet ingested).

2026-05-15  Windshield Config and Windshield Correction columns added
            (pos 32–33).

2026-05-12  Initial document. Core upload endpoint, Swift implementation,
            retry strategy, base CSV format (pos 1–31).
```
