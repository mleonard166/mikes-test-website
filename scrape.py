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
}

EMPTY = {
    "status": "Unknown", "base_depth": "N/A", "new_snow_24h": "N/A",
    "season_total": "N/A", "trails_open": "N/A", "trails_total": "N/A",
    "lifts_open": "N/A", "lifts_total": "N/A", "surface": "N/A",
    "temperature": "N/A", "error": None,
}


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
        # Build a regex that splits on known resort names
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

            # Status
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
# Sunday River
# ---------------------------------------------------------------------------
def scrape_sunday_river():
    d = new_record("sunday_river")
    urls = [
        "https://www.sundayriver.com/mountain-report",
        "https://www.onthesnow.com/maine/sunday-river/skireport",
    ]
    for url in urls:
        try:
            r = fetch(url)
            soup = BeautifulSoup(r.text, 'lxml')
            text = soup.get_text(separator=' ', strip=True)

            if len(text) < 200:
                continue

            if re.search(r'season\s+closed|closed\s+for\s+season', text, re.IGNORECASE):
                d["status"] = "Closed"
            else:
                m = re.search(r'(\d+)\s+of\s+(\d+)\s+trails?\s+open', text, re.IGNORECASE)
                if m:
                    d["trails_open"], d["trails_total"] = m.group(1), m.group(2)
                    d["status"] = "Open" if int(m.group(1)) > 0 else "Closed"

            m = re.search(r'(\d+)\s+of\s+(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
            if m:
                d["lifts_open"], d["lifts_total"] = m.group(1), m.group(2)

            d["base_depth"] = find_first([
                r'base\s+(?:depth)?[:\s]*([\d"\']+)',
                r'([\d"\']+)\s+base',
            ], text)

            d["new_snow_24h"] = find_first([
                r'24[-\s]?hr?[:\s]*([\d"\']+)',
                r'new\s+snow[:\s]*([\d"\']+)',
            ], text)

            m = re.search(r'(\d{1,3})\s*°?\s*F', text, re.IGNORECASE)
            if m:
                d["temperature"] = m.group(1) + "°F"

            d["surface"] = find_first([
                r'surface\s+conditions?[:\s]+([^\n,\.]{5,60})',
                r'grooming[:\s]+([^\n,\.]{5,60})',
            ], text)

            if d["status"] != "Unknown":
                break

        except Exception as e:
            d["error"] = str(e)
            print(f"Sunday River scrape error ({url}): {e}", file=sys.stderr)

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
        elif re.search(r'open\s+today|now\s+open|top.to.bottom|trails?\s+open', text, re.IGNORECASE):
            d["status"] = "Open"

        d["base_depth"]   = find_first([r'base\s*(?:depth)?[:\s]*([\d\.]+)["\s]', r'([\d\.]+)["\s]+base'], text, suffix='"')
        d["new_snow_24h"] = find_first([r'24.?hour[:\s]*([\d\.]+)["\s]', r'new\s+snow[:\s]*([\d\.]+)["\s]'], text, suffix='"')
        d["season_total"] = find_first([r'season\s+total[:\s]*([\d\.]+)["\s]'], text, suffix='"')
        d["surface"]      = find_first([r'surface\s+conditions?[:\s]+([^\n\.]{5,60})'], text)
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
            d["status"] = "Open" if int(m.group(1)) > 0 else "Closed"
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
# Merge new data over existing, preserving last-known values when N/A
# ---------------------------------------------------------------------------
def merge(existing, new_data):
    merged = dict(existing)
    for k, v in new_data.items():
        if v not in (None, "N/A", "Unknown") or k in ("error", "name", "location", "url"):
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

    mountains_new = {
        "black_mountain": bm,
        "cranmore":        cr_merged,
        "attitash":        skinh.get("attitash", new_record("attitash")),
        "wildcat":         skinh.get("wildcat",  new_record("wildcat")),
        "bretton_woods":   skinh.get("bretton_woods", new_record("bretton_woods")),
        "cannon":          skinh.get("cannon",    new_record("cannon")),
        "loon":            loon_merged,
        "sunday_river":    sr,
    }

    # Final merge: preserve last-known values from conditions.json
    mountains_final = {}
    for key, fresh in mountains_new.items():
        prev = old.get(key, {})
        mountains_final[key] = merge(prev, fresh)

    conditions = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "mountains": mountains_final,
    }

    with open("conditions.json", "w") as f:
        json.dump(conditions, f, indent=2)

    print("conditions.json updated")
    for k, v in mountains_final.items():
        print(f"  {v['name']}: {v['status']} | trails {v['trails_open']}/{v['trails_total']} | lifts {v['lifts_open']}/{v['lifts_total']}")
