# TrueNoise — Backend Deployment Guide
## For Xcode Claude and Martin

---

## CURRENT STATUS (as of May 12, 2026)

Everything below is already live. This document is the handoff for
building the iOS app upload function.

- **Public dashboard:** https://truenoise.org
- **API backend:** https://aircraft-noise-tracker-api.onrender.com
- **Database:** Supabase PostgreSQL (permanent, no expiry)
- **GitHub repo:** https://github.com/mdkriza-cpu/truenoise-api

---

## ARCHITECTURE SUMMARY

```
iOS App (Aircraft Noise Tracker)
    │
    │  POST /api/v1/upload-session
    │  (CSV file, multipart/form-data)
    ▼
Render (FastAPI backend)
    │
    │  psycopg / PostgreSQL
    ▼
Supabase (permanent PostgreSQL database)
    │
    │  GET /api/v1/dashboard-summary
    ▼
truenoise.org (GitHub Pages dashboard)
```

---

## API SPECIFICATION FOR XCODE CLAUDE

### Upload Endpoint
```
POST https://aircraft-noise-tracker-api.onrender.com/api/v1/upload-session
```

### Request format
```
Content-Type: multipart/form-data
X-Api-Key: imDQ5QWAD0mWvmltaUV-0Wcup8nR3xwSrx_gEDgmegw
Form field name: "file"
File content: session CSV (comma-separated, same format the app exports)
```

The API key must be sent as the header `X-Api-Key` on every upload request.
Requests without a valid key will receive a 401 Unauthorized response.

### CSV format
The CSV uses comma delimiters (not tab). Column headers must match exactly:
```
Timestamp,Type,dBA Level,Loudness (sone),Loudness Health Impact,
Loudness Level (phon),Loudness Context,Sharpness (acum),
Sharpness Health Impact,Annoyance,Annoyance Health Impact,
Onset Rate (dB/s),Onset Health Impact,Callsign,ICAO24,Type Code,
Type Name,Registration,Operator,Flight Phase,Ground Distance (mi),
Slant Range (mi),Altitude (ft),Bearing,Bearing Compass,
Elevation Angle,Speed (kts),Climb Rate (fpm),Approaching,
Observer Lat,Observer Lon
```

### Swift implementation (URLSession)
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
    
    if httpResponse.statusCode == 200 {
        // Success — parse and log the response
        let result = try JSONDecoder().decode(UploadResponse.self, from: data)
        print("Uploaded session: \(result.sessionId), \(result.observationsInserted) observations")
    } else {
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
- `400` — not a CSV file, or CSV is empty
- `500` — server error (retry later)

---

## RETRY STRATEGY (recommended)

The Render free tier spins down after 15 minutes of inactivity.
First request after idle takes up to 50 seconds.

Recommended approach:
1. Trigger upload from the same "Save" flow that writes the CSV locally
2. On failure (network error or 5xx), store the CSV path in UserDefaults
3. On next app launch, check for queued uploads and retry
4. The backend uses INSERT OR REPLACE so duplicate uploads are safe

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
        } catch {
            remaining.append(path) // keep for next retry
        }
    }
    UserDefaults.standard.set(remaining, forKey: "pendingUploads")
}
```

---

## OTHER API ENDPOINTS

### Health check
```
GET https://aircraft-noise-tracker-api.onrender.com/health
→ {"status": "ok", "timestamp": "2026-05-12T..."}
```

### Dashboard summary (powers truenoise.org)
```
GET https://aircraft-noise-tracker-api.onrender.com/api/v1/dashboard-summary
→ aggregated stats across all sessions
```

### List sessions
```
GET https://aircraft-noise-tracker-api.onrender.com/api/v1/sessions
→ most recent 50 sessions
```

### Session observations
```
GET https://aircraft-noise-tracker-api.onrender.com/api/v1/sessions/{session_id}/observations
→ all raw observations for a specific session
```

---

## DATABASE SCHEMA

### `observations` table
One row per measurement row in the CSV.
Key columns: `session_id`, `timestamp`, `dba_level`, `loudness_sone`,
`annoyance`, `callsign`, `type_code`, `operator`, `flight_phase`

### `sessions` table
One row per uploaded session with pre-computed summary stats.
Key columns: `n65`, `n70`, `n80`, `recovery_deficit`, `event_density`,
`peak_dba`, `peak_loudness_sone`

---

## WHO BENCHMARK COLUMNS (FUTURE)

The `sessions` table already has placeholder columns:
- `who_daily_average_dba`
- `who_exceedance_pct`

When the app computes and sends these, the backend will store them automatically.

---

## NOTES ON NAMING

The app is currently called "Aircraft Noise Tracker" but the platform
is being renamed to support broader environmental noise monitoring
beyond aircraft. The backend API name on Render still shows the old
name but functions correctly. A full rename is planned.

The two public websites:
- **truenoise.org** — data, methodology, dashboard (what we built)
- **stopsevernnoise.org** — community advocacy (to be built separately)


## Windshield Configuration Fields
Two session-level columns to add at the END of every CSV row (after Observer Lon).
Same value repeated for every row in the session.

EXACT column names (case-sensitive):
  Windshield Config           — text: None, Foam, or Fur
  Windshield Correction (dBA) — number: 0.0, 0.7, or 1.8

Insertion loss reference:
  None  -> 0.0 dBA
  Foam  -> 0.7 dBA  (midpoint of 0.6-0.8 measured range)
  Fur   -> 1.8 dBA  (used for all outdoor sessions)

Blank values accepted for backwards compatibility with existing sessions.
Backend reads both fields from the first row of the CSV.

## New CSV Columns — shipped 2026-05-18

Three new session-level columns appended after Windshield Correction (dBA).
Same value repeats for every row in the session. All nullable for backward compatibility.

EXACT column names (case-sensitive):

  Position 32: Measurement Type        — text
  Position 33: Position Description    — text (free-form, may contain commas — quote-escaped)
  Position 34: C-A Delta (dB)          — float, 1 decimal place, may be blank

Measurement Type values:
  Standardized Receptor   — ISO 1996 compliant, tripod, ≥1 m from walls
  Community Receptor      — lived position (porch, balcony etc), includes boundary reinforcement
  Facade-Level            — 0.5–2 m from single wall, WHO L_DEN methodology
  Field Characterization  — mobile/temporary, strategic location
  Hand-held               — no fixed mount

C-A Delta threshold guidance:
  < 15 dB   — clean
  15–25 dB  — possible wind contamination (flag)
  ≥ 25 dB   — strong wind contamination (exclude from analysis)

Supabase columns to add (run before deploying main.py):
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS measurement_type TEXT;
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS position_description TEXT;
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS ca_delta_db REAL;

## Background Baseline — schema only, NOT ingested yet (2026-05-18)

The iOS app now computes LAeq, LA10, LA50, LA90, LA95 per session but the
upload service strips this block before sending to the backend. Schema columns
have been added to sessions table for future use — DO NOT display on dashboard
until Marty sends a separate "go" note with validated data and methodology text.

Baseline block format (local only, not uploaded):
  BACKGROUND BASELINE (from 2-sec peak samples)
  Sample count,<int>
  Sampling duration,<hh:mm or mm:ss>
  LAeq (approx),<float>
  LA10 (exceeded 10% of time),<float>
  LA50 (median),<float>
  LA90 (background floor),<float>
  LA95 (acoustic floor),<float>

Supabase columns to add now (schema only):
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS laeq REAL;
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS la10 REAL;
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS la50 REAL;
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS la90 REAL;
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS la95 REAL;
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS baseline_sample_count INTEGER;
  ALTER TABLE sessions ADD COLUMN IF NOT EXISTS baseline_duration_seconds REAL;
