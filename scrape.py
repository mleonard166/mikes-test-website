import requests
import json
import re
import sys
from datetime import datetime, timezone
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

MOUNTAIN_DEFAULTS = {
    "black_mountain":  {"name": "Black Mountain",   "location": "Jackson, NH",      "url": "https://www.blackmt.com/conditions"},
    "cranmore":        {"name": "Cranmore Mountain", "location": "North Conway, NH",  "url": "https://cranmore.com/snow-report"},
    "attitash":        {"name": "Attitash Mountain", "location": "Bartlett, NH",      "url": "https://www.attitash.com/the-mountain/mountain-conditions/snow-and-weather-report.aspx"},
    "wildcat":         {"name": "Wildcat Mountain",  "location": "Jackson, NH",       "url": "https://www.skiwildcat.com/the-mountain/mountain-conditions/snow-and-weather-report.aspx"},
    "bretton_woods":   {"name": "Bretton Woods",     "location": "Bretton Woods, NH", "url": "https://www.brettonwoods.com/snow-trail-report/"},
    "cannon":          {"name": "Cannon Mountain",   "location": "Franconia, NH",     "url": "https://www.cannonmt.com/mountain-report"},
    "loon":            {"name": "Loon Mountain",     "location": "Lincoln, NH",       "url": "https://www.loonmtn.com/mountain-report"},
    "sunday_river":    {"name": "Sunday River",      "location": "Newry, ME",         "url": "https://www.sundayriver.com/mountain-report"},
    "killington":      {"name": "Killington",        "location": "Killington, VT",    "url": "https://killington.com/mountain-report"},
    "sugarbush":       {"name": "Sugarbush",         "location": "Warren, VT",        "url": "https://www.sugarbush.com/mountain/conditions"},
}

EMPTY = {
    "status": "Unknown", "base_depth": "N/A", "new_snow_24h": "N/A",
    "season_total": "N/A", "trails_open": "N/A", "trails_total": "N/A",
    "lifts_open": "N/A", "lifts_total": "N/A", "surface": "N/A",
    "temperature": "N/A", "error": None,
}

# Fields that represent live operational data — never carry these forward
# from a previous run since they go stale quickly and mislead when kept.
OPERATIONAL_FIELDS = {"status", "trails_open", "lifts_open", "base_depth", "new_snow_24h", "surface", "temperature"}

# NH/ME ski season: roughly November through April
SKI_SEASON_MONTHS = {11, 12, 1, 2, 3, 4}


def is_ski_season():
    return datetime.now(timezone.utc).month in SKI_SEASON_MONTHS


def new_record(key):
    r = dict(EMPTY)
    r.update(MOUNTAIN_DEFAULTS[key])
    return r


def fetch(url, timeout=30):
    return requests.get(url, headers=HEADERS, timeout=timeout)


def find_first(patterns, text, group=1, suffix=""):
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(group).strip() + suffix
    return "N/A"


def derive_status(d):
    """
    Apply consistent status logic after all fields are set.
    Priority order (highest to lowest):
      1. Off-season → Closed
      2. Explicit "Season Closed" surface text → Closed
      3. Both trails AND lifts are 0 → Closed
      4. trails_open > 0 AND lifts_open > 0 → Open (positive evidence required)
      5. trails_open > 0 XOR lifts_open > 0 → trust the count
      6. status already set by scraper → keep it
      7. fallback → Unknown
    """
    if not is_ski_season():
        d["status"] = "Closed"
        return d

    if d.get("surface") and re.search(r'season\s+closed', d["surface"], re.IGNORECASE):
        d["status"] = "Closed"
        return d

    try:
        trails = int(d["trails_open"]) if d.get("trails_open") not in (None, "N/A") else None
        lifts  = int(d["lifts_open"])  if d.get("lifts_open")  not in (None, "N/A") else None
    except (ValueError, TypeError):
        trails = lifts = None

    if trails is not None and lifts is not None:
        if trails == 0 and lifts == 0:
            d["status"] = "Closed"
        elif trails > 0 or lifts > 0:
            d["status"] = "Open"
        return d

    # Only one count is available
    if trails is not None:
        d["status"] = "Open" if trails > 0 else "Closed"
        return d
    if lifts is not None:
        d["status"] = "Open" if lifts > 0 else "Closed"
        return d

    # No counts to corroborate — can't prove it's open, so treat as Closed.
    # Text-detected "Closed" is kept as-is.
    if d.get("status") != "Closed":
        d["status"] = "Closed"
    return d


# ---------------------------------------------------------------------------
# SkiNH aggregator — fetches once, returns dict keyed by slug
# ---------------------------------------------------------------------------
def scrape_skinh():
    results = {k: new_record(k) for k in ["attitash", "wildcat", "bretton_woods", "cannon", "loon", "cranmore"]}
    try:
        r = fetch("https://www.skinh.com/conditions")
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator='\n', strip=True)

        # Map display name fragments to our keys
        name_map = [
            ("attitash",     ["Attitash"]),
            ("wildcat",      ["Wildcat"]),
            ("bretton_woods",["Bretton Woods", "BrettonWoods"]),
            ("cannon",       ["Cannon"]),
            ("loon",         ["Loon"]),
            ("cranmore",     ["Cranmore"]),
        ]

        # Split text into per-resort chunks using resort names as delimiters
        all_names = [n for _, names in name_map for n in names]
        split_pat = r'(?=' + '|'.join(re.escape(n) for n in all_names) + r')'
        chunks = re.split(split_pat, text, flags=re.IGNORECASE)

        for chunk in chunks:
            matched_key = None
            for key, names in name_map:
                if any(chunk.strip().startswith(n) for n in names):
                    matched_key = key
                    break
                if any(re.search(r'\b' + re.escape(n) + r'\b', chunk[:80], re.IGNORECASE) for n in names):
                    matched_key = key
                    break
            if not matched_key:
                continue

            d = results[matched_key]

            # Status — start with text signals; derive_status() will finalize
            if re.search(r'Closed\s+for\s+Snow\s+Sports|Season\s+Closed|CLOSED', chunk, re.IGNORECASE):
                d["status"] = "Closed"
            elif re.search(r'\bOpen\b', chunk, re.IGNORECASE):
                d["status"] = "Open"

            # Base depth
            m = re.search(r'Base\s+Depth[:\s]+([^\n]+)', chunk, re.IGNORECASE)
            if m:
                d["base_depth"] = m.group(1).strip()

            # 24hr snow
            m = re.search(r'New\s+Snow\s*\(?24\s*hr[s)]*\)?[:\s]+([^\n]+)', chunk, re.IGNORECASE)
            if m:
                d["new_snow_24h"] = m.group(1).strip()

            # Trails
            m = re.search(r'Trails?\s+Open[:\s]+(\d+)\s+of\s+(\d+)', chunk, re.IGNORECASE)
            if m:
                d["trails_open"] = m.group(1)
                d["trails_total"] = m.group(2)

            # Lifts
            m = re.search(r'Lifts?\s+Open[:\s]+(\d+)\s+of\s+(\d+)', chunk, re.IGNORECASE)
            if m:
                d["lifts_open"] = m.group(1)
                d["lifts_total"] = m.group(2)

            # Surface / conditions
            m = re.search(r'Conditions?[:\s]+"?([^"\n]{3,60})"?', chunk, re.IGNORECASE)
            if m:
                d["surface"] = m.group(1).strip().strip('"')

            # Temperature
            m = re.search(r'(\d{1,3})\s*°?\s*F', chunk, re.IGNORECASE)
            if m:
                d["temperature"] = m.group(1) + "°F"

    except Exception as e:
        print(f"SkiNH scrape error: {e}", file=sys.stderr)
        for d in results.values():
            if not d["error"]:
                d["error"] = str(e)

    return results


# ---------------------------------------------------------------------------
# Loon — Global Conditions Feed (detailed)
# ---------------------------------------------------------------------------
def scrape_loon():
    d = new_record("loon")
    try:
        r = fetch("https://globalconditionsfeed.azurewebsites.net/lm/printablereports")
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator=' ', strip=True)

        if re.search(r'season\s+closed|closed\s+for\s+season', text, re.IGNORECASE):
            d["status"] = "Closed"
        elif re.search(r'trails?\s+open|lifts?\s+open', text, re.IGNORECASE):
            d["status"] = "Open"

        m = re.search(r'(\d+)\s+of\s+(\d+)\s+trails?\s+open', text, re.IGNORECASE)
        if m:
            d["trails_open"], d["trails_total"] = m.group(1), m.group(2)

        m = re.search(r'(\d+)\s+of\s+(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
        if m:
            d["lifts_open"], d["lifts_total"] = m.group(1), m.group(2)

        d["base_depth"] = find_first([
            r'base\s+depth[:\s]+([\d"\'–\-]+(?:\s*[-–]\s*[\d"\']+)?)',
            r'base[:\s]+([\d"\'–\-]+)',
        ], text)

        d["new_snow_24h"] = find_first([
            r'24[-\s]?hour[:\s]+([\d"\']+)',
            r'new\s+snow[:\s]+([\d"\']+)',
        ], text)

        d["surface"] = find_first([
            r'surface\s+conditions?[:\s]+([^\n,\.]{5,60})',
            r'conditions?[:\s]+([A-Za-z ,]+?)(?:\s*\n|\s{2,})',
        ], text)

        m = re.search(r'(\d{1,3})\s*°?\s*F', text, re.IGNORECASE)
        if m:
            d["temperature"] = m.group(1) + "°F"

    except Exception as e:
        d["error"] = str(e)
        print(f"Loon scrape error: {e}", file=sys.stderr)
    return d


# ---------------------------------------------------------------------------
# Sunday River — Global Conditions Feed (same system as Loon)
# ---------------------------------------------------------------------------
def scrape_sunday_river():
    d = new_record("sunday_river")

    # Primary: Global Conditions Feed (Boyne Resorts shared backend)
    try:
        r = fetch("https://globalconditionsfeed.azurewebsites.net/sr/printablereports")
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'lxml')
            text = soup.get_text(separator=' ', strip=True)

            if re.search(r'season\s+closed|closed\s+for\s+season', text, re.IGNORECASE):
                d["status"] = "Closed"
            elif re.search(r'trails?\s+open|lifts?\s+open', text, re.IGNORECASE):
                d["status"] = "Open"

            m = re.search(r'(\d+)\s+of\s+(\d+)\s+trails?\s+open', text, re.IGNORECASE)
            if m:
                d["trails_open"], d["trails_total"] = m.group(1), m.group(2)

            m = re.search(r'(\d+)\s+of\s+(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
            if m:
                d["lifts_open"], d["lifts_total"] = m.group(1), m.group(2)

            d["base_depth"] = find_first([
                r'base\s+depth[:\s]+([\d"\'–\-]+(?:\s*[-–]\s*[\d"\']+)?)',
                r'base[:\s]+([\d"\'–\-]+)',
            ], text)

            d["new_snow_24h"] = find_first([
                r'24[-\s]?hour[:\s]+([\d"\']+)',
                r'new\s+snow[:\s]+([\d"\']+)',
            ], text)

            d["surface"] = find_first([
                r'surface\s+conditions?[:\s]+([^\n,\.]{5,60})',
                r'conditions?[:\s]+([A-Za-z ,]+?)(?:\s*\n|\s{2,})',
            ], text)

            m = re.search(r'(\d{1,3})\s*°?\s*F', text, re.IGNORECASE)
            if m:
                d["temperature"] = m.group(1) + "°F"

            print(f"Sunday River feed: status={d['status']} trails={d['trails_open']} lifts={d['lifts_open']}", file=sys.stderr)
            return d
        else:
            print(f"Sunday River feed returned {r.status_code}, falling back.", file=sys.stderr)
    except Exception as e:
        print(f"Sunday River feed error: {e}", file=sys.stderr)

    # Fallback: main website (JS-rendered — only closed-season text is reliable;
    # do NOT extract temperature since the page source contains stray numbers)
    try:
        r = fetch("https://www.sundayriver.com/mountain-report")
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator=' ', strip=True)

        if len(text) < 200:
            return d

        if re.search(r'season\s+closed|closed\s+for\s+season', text, re.IGNORECASE):
            d["status"] = "Closed"

        # Counts are not reliably present in pre-JS HTML — skip rather than guess
        d["surface"] = find_first([
            r'surface\s+conditions?[:\s]+([^\n,\.]{5,60})',
        ], text)

    except Exception as e:
        d["error"] = str(e)
        print(f"Sunday River fallback error: {e}", file=sys.stderr)

    return d


# ---------------------------------------------------------------------------
# Black Mountain (original)
# ---------------------------------------------------------------------------
def scrape_black_mountain():
    d = new_record("black_mountain")
    try:
        r = fetch("https://www.blackmt.com/conditions")
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator=' ', strip=True)

        if re.search(r'season\s+closed|closed\s+for\s+the\s+season', text, re.IGNORECASE):
            d["status"] = "Closed"
        elif re.search(r'open\s+today|now\s+open|top.to.bottom', text, re.IGNORECASE):
            # Note: "trails open" is intentionally excluded — it matches nav links
            # and section headers even when nothing is actually open. Status is
            # derived from the numeric trail/lift counts extracted below.
            d["status"] = "Open"

        d["base_depth"]   = find_first([r'base\s*(?:depth)?[:\s]*([\d\.]+)["\s]', r'([\d\.]+)["\s]+base'], text, suffix='"')
        d["new_snow_24h"] = find_first([r'24.?hour[:\s]*([\d\.]+)["\s]', r'new\s+snow[:\s]*([\d\.]+)["\s]'], text, suffix='"')
        d["season_total"] = find_first([r'season\s+total[:\s]*([\d\.]+)["\s]'], text, suffix='"')
        d["surface"]      = find_first([r'surface\s+conditions?[:\s]+((?:packed|groomed|loose|wet|icy|spring|corn|powder|variable|crusty|machine|smooth|soft)[^\n\.]{0,50})'], text)
        d["temperature"]  = find_first([r'(\d{1,3})\s*°?\s*F'], text, suffix='°F')

        m = re.search(r'(\d+)\s+of\s+(\d+)\s+trails?\s+open', text, re.IGNORECASE)
        if m:
            d["trails_open"], d["trails_total"] = m.group(1), m.group(2)
        else:
            m = re.search(r'(\d+)\s+trails?\s+open', text, re.IGNORECASE)
            if m: d["trails_open"] = m.group(1)

        m = re.search(r'(\d+)\s+of\s+(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
        if m:
            d["lifts_open"], d["lifts_total"] = m.group(1), m.group(2)
        else:
            m = re.search(r'(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
            if m: d["lifts_open"] = m.group(1)

    except Exception as e:
        d["error"] = str(e)
        print(f"Black Mountain scrape error: {e}", file=sys.stderr)
    return d


# ---------------------------------------------------------------------------
# Cranmore (original)
# ---------------------------------------------------------------------------
def scrape_cranmore():
    d = new_record("cranmore")
    try:
        r = fetch("https://cranmore.com/snow-report")
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator=' ', strip=True)

        m = re.search(r'(\d+)\s+of\s+(\d+)\s+trails?\s+open', text, re.IGNORECASE)
        if m:
            d["trails_open"], d["trails_total"] = m.group(1), m.group(2)
        elif re.search(r'season\s+closed', text, re.IGNORECASE):
            d["status"] = "Closed"

        m = re.search(r'(\d+)\s+of\s+(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
        if m:
            d["lifts_open"], d["lifts_total"] = m.group(1), m.group(2)

        d["base_depth"]   = find_first([r'base\s*(?:depth)?[:\s]*([\d\.]+)["\s]', r'([\d\.]+)["\s]+base'], text, suffix='"')
        d["new_snow_24h"] = find_first([r'24.?hr?[:\s]*([\d\.]+)["\s]', r'new\s+snow[:\s]*([\d\.]+)["\s]'], text, suffix='"')
        d["season_total"] = find_first([r'season\s+total[:\s]*([\d\.]+)["\s]'], text, suffix='"')
        d["temperature"]  = find_first([r'(\d{1,3})\s*°?\s*F'], text, suffix='°F')

    except Exception as e:
        d["error"] = str(e)
        print(f"Cranmore scrape error: {e}", file=sys.stderr)
    return d


# ---------------------------------------------------------------------------
# Killington (SSR page — data is in the initial HTML)
# ---------------------------------------------------------------------------
def scrape_killington():
    d = new_record("killington")
    try:
        r = fetch("https://killington.com/mountain-report")
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator='\n', strip=True)

        if re.search(r'season\s+closed|closed\s+for\s+the\s+season', text, re.IGNORECASE):
            d["status"] = "Closed"

        # Lifts: "Scheduled Lifts\n2/19" or "2/19 Scheduled Lifts"
        m = re.search(r'Scheduled\s+Lifts\s+(\d+)/(\d+)', text, re.IGNORECASE)
        if m:
            d["lifts_open"], d["lifts_total"] = m.group(1), m.group(2)

        # Trails: "Open Trails\n3/155"
        m = re.search(r'Open\s+Trails\s+(\d+)/(\d+)', text, re.IGNORECASE)
        if m:
            d["trails_open"], d["trails_total"] = m.group(1), m.group(2)

        # 24hr snow: "24 HOUR\n0\""
        m = re.search(r'24\s+HOUR\s+(\d+)"', text, re.IGNORECASE)
        if not m:
            m = re.search(r'24[-\s]?hour[:\s]+([\d]+)"', text, re.IGNORECASE)
        if m:
            d["new_snow_24h"] = m.group(1) + '"'

        # Season total: "SEASON\n5\""
        m = re.search(r'\bSEASON\b\s+(\d+)"', text, re.IGNORECASE)
        if m:
            d["season_total"] = m.group(1) + '"'

        # Base depth
        m = re.search(r'Base\s+Depth[:\s]+([\d]+)"', text, re.IGNORECASE)
        if not m:
            m = re.search(r'([\d]+)"\s+Base', text, re.IGNORECASE)
        if m:
            d["base_depth"] = m.group(1) + '"'

        # Temperature: appears as "40°" (no F suffix) in navbar
        m = re.search(r'(\d{1,3})°F', text, re.IGNORECASE)
        if not m:
            m = re.search(r'(\d{1,3})°', text)
        if m:
            d["temperature"] = m.group(1) + "°F"

        # Surface conditions
        d["surface"] = find_first([
            r'Surface\s+Conditions?[:\s]+([^\n]{5,60})',
            r'Conditions?[:\s]+([A-Za-z ,/]+?)(?:\n|\s{2,})',
        ], text)

        print(f"Killington: status={d['status']} trails={d['trails_open']} lifts={d['lifts_open']}", file=sys.stderr)
    except Exception as e:
        d["error"] = str(e)
        print(f"Killington scrape error: {e}", file=sys.stderr)
    return d


# ---------------------------------------------------------------------------
# Sugarbush (Alterra — mtnpowder.com JSON API, token in page source)
# ---------------------------------------------------------------------------
def scrape_sugarbush():
    d = new_record("sugarbush")
    try:
        # The page embeds the mtnpowder bearer token in its inline scripts / __NEXT_DATA__
        r = fetch("https://www.sugarbush.com/mountain/conditions")
        html = r.text

        # Extract bearer token from page source
        token_m = re.search(r'bearer_token[=&"\s:]+([A-Za-z0-9_\-\.]{20,})', html)
        if token_m:
            token = token_m.group(1)
            api_url = f"https://mtnpowder.com/feed/v3.json?bearer_token={token}&resortId[]=70"
            api_r = fetch(api_url)
            if api_r.status_code == 200:
                data = api_r.json()
                resort = None
                # v3 response is a list or has a "resorts" key
                if isinstance(data, list):
                    resort = data[0] if data else None
                elif isinstance(data, dict):
                    resorts = data.get("resorts") or data.get("data") or []
                    resort = resorts[0] if resorts else data

                if resort:
                    cs = resort.get("CurrentConditions") or resort.get("currentConditions") or resort
                    d["trails_open"]  = str(cs.get("OpenTrails") or cs.get("openTrails") or "N/A")
                    d["trails_total"] = str(cs.get("TotalTrails") or cs.get("totalTrails") or "N/A")
                    d["lifts_open"]   = str(cs.get("OpenLifts") or cs.get("openLifts") or "N/A")
                    d["lifts_total"]  = str(cs.get("TotalLifts") or cs.get("totalLifts") or "N/A")
                    base = cs.get("BaseDepth") or cs.get("baseDepth")
                    if base is not None:
                        d["base_depth"] = str(base) + '"'
                    snow24 = cs.get("NewSnow24Hours") or cs.get("newSnow24Hours") or cs.get("SnowLast24Hours")
                    if snow24 is not None:
                        d["new_snow_24h"] = str(snow24) + '"'
                    season = cs.get("SeasonTotal") or cs.get("seasonTotal") or cs.get("SnowSeasonTotal")
                    if season is not None:
                        d["season_total"] = str(season) + '"'
                    temp = cs.get("Temperature") or cs.get("temperature")
                    if temp is not None:
                        d["temperature"] = str(temp) + "°F"
                    surf = cs.get("SurfaceConditions") or cs.get("surfaceConditions") or cs.get("Conditions")
                    if surf:
                        d["surface"] = str(surf)
                    print(f"Sugarbush API: trails={d['trails_open']} lifts={d['lifts_open']}", file=sys.stderr)
                    return d
            print(f"Sugarbush API returned {api_r.status_code}", file=sys.stderr)

        # Fallback: parse visible text from pre-rendered HTML
        soup = BeautifulSoup(html, 'lxml')
        text = soup.get_text(separator='\n', strip=True)

        if re.search(r'season\s+closed|closed\s+for\s+the\s+season', text, re.IGNORECASE):
            d["status"] = "Closed"

        m = re.search(r'(\d+)\s*(?:of\s*)?/\s*(\d+)\s+(?:open\s+)?trails?', text, re.IGNORECASE)
        if not m:
            m = re.search(r'Open\s+Trails?\s+(\d+)\s*/\s*(\d+)', text, re.IGNORECASE)
        if m:
            d["trails_open"], d["trails_total"] = m.group(1), m.group(2)

        m = re.search(r'(\d+)\s*(?:of\s*)?/\s*(\d+)\s+(?:open\s+)?lifts?', text, re.IGNORECASE)
        if not m:
            m = re.search(r'Open\s+Lifts?\s+(\d+)\s*/\s*(\d+)', text, re.IGNORECASE)
        if m:
            d["lifts_open"], d["lifts_total"] = m.group(1), m.group(2)

        m = re.search(r'(\d{1,3})°F', text)
        if m:
            d["temperature"] = m.group(1) + "°F"

        print(f"Sugarbush fallback: status={d['status']} trails={d['trails_open']}", file=sys.stderr)
    except Exception as e:
        d["error"] = str(e)
        print(f"Sugarbush scrape error: {e}", file=sys.stderr)
    return d


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------
def merge(existing, new_data):
    """
    Merge new_data on top of existing, but with these rules:
    - Static fields (name, location, url, error) always update.
    - OPERATIONAL_FIELDS always use the fresh value, even if it's "N/A" or
      "Unknown" — stale open/closed status from a previous season is worse
      than showing unknown.
    - All other fields carry forward from existing when fresh is "N/A"/"Unknown".
    """
    merged = {**EMPTY, **existing}
    for k, v in new_data.items():
        if k in ("error", "name", "location", "url"):
            merged[k] = v
        elif k in OPERATIONAL_FIELDS:
            # Always use fresh operational data — never preserve last season's status
            merged[k] = v
        elif v not in (None, "N/A", "Unknown"):
            merged[k] = v
    return merged


def load_existing():
    try:
        with open("conditions.json") as f:
            return json.load(f)
    except Exception:
        return {"mountains": {}}


if __name__ == "__main__":
    existing = load_existing()
    old = existing.get("mountains", {})

    if not is_ski_season():
        print(f"Off-season (month {datetime.now(timezone.utc).month}): skipping live scrape, all resorts marked Closed.", file=sys.stderr)

    # Scrape SkiNH once for all NH resorts
    print("Scraping SkiNH...", file=sys.stderr)
    skinh = scrape_skinh()

    # Scrape individual sources
    print("Scraping Loon feed...", file=sys.stderr)
    loon = scrape_loon()
    # Merge Loon: SkiNH base + feed detail
    loon_merged = merge(skinh.get("loon", new_record("loon")), loon)

    print("Scraping Black Mountain...", file=sys.stderr)
    bm = scrape_black_mountain()

    print("Scraping Cranmore...", file=sys.stderr)
    cr = scrape_cranmore()
    # Merge Cranmore: SkiNH base + direct
    cr_merged = merge(skinh.get("cranmore", new_record("cranmore")), cr)

    print("Scraping Sunday River...", file=sys.stderr)
    sr = scrape_sunday_river()

    print("Scraping Killington...", file=sys.stderr)
    kil = scrape_killington()

    print("Scraping Sugarbush...", file=sys.stderr)
    sug = scrape_sugarbush()

    mountains_new = {
        "black_mountain": bm,
        "cranmore":        cr_merged,
        "attitash":        skinh.get("attitash", new_record("attitash")),
        "wildcat":         skinh.get("wildcat",  new_record("wildcat")),
        "bretton_woods":   skinh.get("bretton_woods", new_record("bretton_woods")),
        "cannon":          skinh.get("cannon",    new_record("cannon")),
        "loon":            loon_merged,
        "sunday_river":    sr,
        "killington":      kil,
        "sugarbush":       sug,
    }

    # Final merge: carry forward only non-operational fields (e.g. season_total, trails_total)
    mountains_final = {}
    for key, fresh in mountains_new.items():
        prev = old.get(key, {})
        merged = merge(prev, fresh)
        # Apply consistent status logic as the final word
        mountains_final[key] = derive_status(merged)

    conditions = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "mountains": mountains_final,
    }

    with open("conditions.json", "w") as f:
        json.dump(conditions, f, indent=2)

    print("conditions.json updated")
    for k, v in mountains_final.items():
        print(f"  {v['name']}: {v['status']} | trails {v['trails_open']}/{v['trails_total']} | lifts {v['lifts_open']}/{v['lifts_total']}")
