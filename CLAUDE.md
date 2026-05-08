# PURSUE MCP Server

An MCP server that aggregates declassified U.S. government UAP/UFO records — primarily from the newly launched PURSUE archive at war.gov/UFO, with cross-source coverage of AARO, the FBI Vault, and NARA.

---

## Current upstream status (as of 2026-05-08)

- **PURSUE launched today** (May 8, 2026) at https://www.war.gov/UFO/. The page is live but the catalog UI shows **"0 Files"** — the JSON feed is either not yet populated or not yet wired up. Multiple news outlets called the site "glitchy in the early going."
- DOW says new tranches will drop **every few weeks** on a rolling basis.
- The slideshow at `https://www.war.gov/portals/1/Interactive/2026/UFO/Slideshow/` shows ~16 example assets with a consistent naming pattern: `DOW-UAP-PR{NN}-Unresolved-UAP-Report-{Location}-{Year}.jpg`. Visible numbers: PR19, PR26, PR34, PR35, PR38, PR43, PR45, PR46, PR49 — gaps imply ~49+ press-release-style records queued.
- One news site (Leonard David) showed a deep-link anchor `#65_HS1-834228961_62-HQ-83894_Section_10`, which is FBI HQ file convention (`62-HQ-83894`). Records will carry **structured originating-agency IDs** worth preserving.
- The site's filter UI exposes these fields: **Agency, Release Date, Incident Date, Incident Location, Type**. Mirror this schema.

**Implication for build:** PURSUE alone won't have enough data to be useful for weeks. Build against sister sources first.

---

## User context

- Senior Cybersecurity Sales Engineer, technical, comfortable with Python and APIs.
- Prefers concise communication. Skip preamble, lead with substance.
- Comfortable iterating; prefers shipping a working v1 over a polished v0.

---

## Tech stack (decided)

- **Language:** Python 3.11+
- **MCP framework:** FastMCP
- **HTTP:** httpx (async)
- **HTML parsing:** BeautifulSoup4
- **PDF text:** pdfplumber (native), pytesseract (OCR fallback for scans)
- **Cache/index:** SQLite with FTS5 for full-text search
- **Project mgmt:** uv or poetry, your call

Local cache is non-negotiable — gov sites rate-limit aggressively and you don't want to re-OCR a 13MB scanned PDF on every query.

---

## Data sources (in build order)

### 1. AARO (https://www.aaro.mil) — start here
Mature, structured, has real data today.
- `/UAP-Cases/Official-UAP-Imagery/`
- `/UAP-Cases/UAP-Case-Resolution-Reports/`
- `/UAP-Cases/UAP-Reporting-Trends/`
- `/UAP-Records/`
- `/EFOIA-Reading-Room/`
- `/Congressional-Press-Products/`

### 2. FBI Vault (https://vault.fbi.gov/UFO)
16-part historical UFO collection, plus separate Roswell and Guy Hottel collections. Decades-old scans — OCR will be rough.

### 3. NARA UAP records (https://www.archives.gov/research/topics/uaps)
National Archives UAP topic page; collections referenced from AARO.

### 4. PURSUE (https://www.war.gov/UFO/) — last
Add once the JSON feed is reachable. **Discovery step:** open Chrome DevTools → Network tab → filter on XHR/Fetch → reload the page → find the catalog endpoint. war.gov runs on DotNetNuke, so expect something like `/DesktopModules/.../API/...`. Don't path-guess; capture the real URL.

### Asset hosting
DoD PDFs typically live on `media.defense.gov`, not the agency site itself. Plan for cross-domain fetches.

---

## Normalized record schema

Every source gets normalized to this shape before indexing:

```python
{
  "id": "pursue:DOW-UAP-PR46",          # namespaced primary key
  "source": "pursue",                    # pursue | aaro | fbi_vault | nara
  "originating_agency": "INDOPACOM",
  "originating_file_id": "62-HQ-83894",  # when present (e.g. FBI HQ files)
  "title": "Unresolved UAP Report - INDOPACOM 2024",
  "type": "report",                      # report | image | video | transcript | sketch | memo
  "release_date": "2026-05-08",
  "incident_date": "2024-XX-XX",         # often partial/approximate; preserve as ISO-ish string
  "incident_location": {
    "raw": "near Japan",
    "country": "JP",                     # ISO-3166 alpha-2 when resolvable
    "region": "INDOPACOM",               # combatant command or similar
    "lat": None, "lon": None             # populated when geocodable
  },
  "classification": "Unresolved",
  "attachments": [
    {"kind": "pdf", "url": "...", "sha256": "...", "ocr_done": True},
    {"kind": "image", "url": "...", "sha256": "..."}
  ],
  "summary": "...",                      # LLM-generated, regenerable
  "raw_text": "...",                     # OCR'd or extracted body
  "tags": ["infrared", "football-shaped", "maritime"],
  "fetched_at": "2026-05-08T12:34:56Z",
  "source_url": "https://www.war.gov/UFO/#..."
}
```

**Dedupe rule:** content hash on `attachments[].sha256`, not URL. The same Apollo 17 image will likely appear in both NASA and DOW tranches.

---

## Tool list

### PURSUE-specific
- `pursue_list_records(agency?, type?, location?, incident_date_range?, release_date_range?, limit?, cursor?)` — paginated catalog query mirroring the site's filter fields
- `pursue_get_record(record_id)` — full metadata + asset URLs
- `pursue_get_record_content(record_id, format='text'|'images'|'metadata', page_range?)` — extracts content; auto-OCRs scans
- `pursue_search_fulltext(query, filters?)` — FTS5 over OCR'd + native text
- `pursue_list_tranches()` — release waves with dates and counts
- `pursue_diff_since(timestamp)` — what's new since last sync (**killer feature** for rolling-release archive)

### Cross-source
- `aaro_list_cases(category, filters?)` / `aaro_get_case(case_id)`
- `fbi_vault_search(query, collection='UFO'|'Roswell'|'Hottel')`
- `nara_uap_search(query, filters?)`

### Aggregation
- `unified_search(query, sources?, filters?)` — fan-out across enabled sources, normalized result shape
- `incidents_by_geo(bbox?, date_range?)` — for mapping
- `incidents_by_timeline(date_range?, agency?)` — chronological view

---

## Resources (MCP read-only)

- `pursue://catalog` — full catalog manifest
- `pursue://stats` — release counts, agency breakdown, latest tranche date
- `pursue://record/{id}` — single record
- `unified://incident/{source}/{id}` — single record across any source

---

## Prompts (MCP-provided)

- `summarize-record` — given a record_id, structured summary
- `compare-incidents` — same region or close in time
- `analyze-tranche` — what's notable in the latest release
- `track-new-since` — diff against a timestamp, summarize what's new

---

## Gotchas to plan around

1. **Empty-state handling.** Today's PURSUE catalog is empty. Tools must return useful empty responses with metadata about expected next tranche, not exceptions.
2. **OCR quality on old scans.** 1950s FBI files are rough — budget time for cleanup heuristics (column merging, header/footer stripping, redaction-bar detection).
3. **DotNetNuke endpoint discovery.** Don't guess the PURSUE JSON URL — sniff it from DevTools once it serves real data. Document the exact endpoint in code comments when found.
4. **Asset dedupe.** Hash everything. Apollo 17 imagery especially will appear under multiple agencies.
5. **Rate limiting.** Set a polite User-Agent identifying the project, add exponential backoff, respect `robots.txt`, cache aggressively.
6. **Schema flexibility.** DOW says future tranches may include unfamiliar file types. Keep `attachments[].kind` open-ended with a sensible default of `"other"`.
7. **Partial dates.** Incident dates are frequently "September 2023" or "2024" only. Don't coerce to full ISO; preserve as a flexible string and parse on read.
8. **Geocoding.** Locations are often regional ("near Japan", "Greece's airspace", "western United States", combatant command names like INDOPACOM). Build a regional lookup table before reaching for a geocoder.

---

## Open questions (resolve before locking schema)

- What's the actual PURSUE catalog endpoint? — discover via DevTools
- Does PURSUE include video files (.mp4) or only stills? — first tranche should clarify
- Are FBI records on PURSUE deduplicated against vault.fbi.gov, or are they re-released? — compare hashes once both are indexed
- Will AARO start cross-referencing PURSUE record IDs? — watch for schema changes
- Does the user want Claude Desktop integration in v1, or just a CLI/test harness? — confirm with user

---

## Reference URLs

- PURSUE landing: https://www.war.gov/UFO/
- DOW press release: https://www.war.gov/News/Releases/Release/Article/4480582/ (currently 404 — may be a redirect issue, check later)
- AARO: https://www.aaro.mil/
- AARO Historical Record Vol. 1: https://www.aaro.mil/Portals/136/PDFs/AARO_Historical_Record_Report_Vol_1_2024.pdf
- FBI Vault UFO: https://vault.fbi.gov/UFO
- NARA UAPs: https://www.archives.gov/research/topics/uaps
- Slideshow asset path pattern: `https://www.war.gov/portals/1/Interactive/2026/UFO/Slideshow/DOW-UAP-PR{NN}-Unresolved-UAP-Report-{Location}-{Year}.jpg`

---

## Conventions for Claude Code working in this repo

- Default to **paraphrasing**, not quoting, when summarizing source documents (these are gov records but news commentary about them is copyrighted).
- Prefer **adding** new tools over modifying existing ones once the schema is published — downstream users will pin against the tool surface.
- Every external fetch goes through a single rate-limited client wrapper. No ad-hoc `httpx.get()` calls scattered through the codebase.
- Log every upstream parsing failure with the raw response saved to disk. PURSUE's schema will drift; we'll need the artifacts to debug.
- Tests should mock at the HTTP layer, not the parser layer — so schema drift surfaces as a test failure on real fixtures, not a silent pass.
