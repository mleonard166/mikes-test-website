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

def find_first(patterns, text, group=1, suffix=""):
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(group).strip() + suffix
    return "N/A"

def scrape_black_mountain():
    data = {
        "name": "Black Mountain",
        "location": "Jackson, NH",
        "url": "https://www.blackmt.com/conditions",
        "status": "Unknown",
        "base_depth": "N/A",
        "new_snow_24h": "N/A",
        "season_total": "N/A",
        "trails_open": "N/A",
        "trails_total": "N/A",
        "lifts_open": "N/A",
        "lifts_total": "N/A",
        "surface": "N/A",
        "temperature": "N/A",
        "error": None
    }

    try:
        r = requests.get("https://www.blackmt.com/conditions", headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator=' ', strip=True)

        if re.search(r'season\s+closed|closed\s+for\s+the\s+season', text, re.IGNORECASE):
            data["status"] = "Closed"
        elif re.search(r'open\s+today|now\s+open|top.to.bottom|trails?\s+open', text, re.IGNORECASE):
            data["status"] = "Open"

        data["base_depth"] = find_first([
            r'base\s*depth[:\s]*(\d+[\d\.]*)["\s]',
            r'base[:\s]+(\d+[\d\.]*)["\'`]',
            r'(\d+)["\s]+base',
        ], text, suffix='"')

        data["new_snow_24h"] = find_first([
            r'24.?hour[:\s]*(\d+[\d\.]*)["\s]',
            r'24.?hr[:\s]*(\d+[\d\.]*)["\s]',
            r'new\s+snow[:\s]*(\d+[\d\.]*)["\s]',
            r'overnight[:\s]*(\d+[\d\.]*)["\s]',
        ], text, suffix='"')

        data["season_total"] = find_first([
            r'season\s+total[:\s]*(\d+[\d\.]*)["\s]',
            r'seasonal\s+total[:\s]*(\d+[\d\.]*)["\s]',
            r'(\d+)["\s]+season\s+total',
        ], text, suffix='"')

        # Trails open / total  (e.g. "7 of 47 trails open" or "7 trails open")
        m = re.search(r'(\d+)\s+of\s+(\d+)\s+trails?\s+open', text, re.IGNORECASE)
        if m:
            data["trails_open"] = m.group(1)
            data["trails_total"] = m.group(2)
        else:
            m = re.search(r'(\d+)\s+trails?\s+open', text, re.IGNORECASE)
            if m:
                data["trails_open"] = m.group(1)
            m2 = re.search(r'(\d+)\s+(?:percent|%)\s+open', text, re.IGNORECASE)
            pct_m = re.search(r'(\d+)%?\s+open', text, re.IGNORECASE)

        # Lifts open / total
        m = re.search(r'(\d+)\s+of\s+(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
        if m:
            data["lifts_open"] = m.group(1)
            data["lifts_total"] = m.group(2)
        else:
            m = re.search(r'(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
            if m:
                data["lifts_open"] = m.group(1)
            m = re.search(r'lifts?\s+open[:\s]*(\d+)', text, re.IGNORECASE)
            if m:
                data["lifts_open"] = m.group(1)

        data["surface"] = find_first([
            r'surface\s+conditions?[:\s]+([^\n\.]{5,60})',
            r'conditions?[:\s]+([A-Za-z &,]{5,50})',
        ], text)

        data["temperature"] = find_first([
            r'(\d+)\s*°\s*F',
            r'temp(?:erature)?[:\s]*(\d+)[°\s]*F',
            r'high[:\s]*(\d+)[°\s]*F',
        ], text, suffix='°F')

    except Exception as e:
        data["error"] = str(e)
        print(f"Black Mountain scrape error: {e}", file=sys.stderr)

    return data


def scrape_cranmore():
    data = {
        "name": "Cranmore Mountain",
        "location": "North Conway, NH",
        "url": "https://cranmore.com/snow-report",
        "status": "Unknown",
        "base_depth": "N/A",
        "new_snow_24h": "N/A",
        "season_total": "N/A",
        "trails_open": "N/A",
        "trails_total": "N/A",
        "lifts_open": "N/A",
        "lifts_total": "N/A",
        "surface": "N/A",
        "temperature": "N/A",
        "error": None
    }

    try:
        r = requests.get("https://cranmore.com/snow-report", headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator=' ', strip=True)

        if re.search(r'season\s+closed|closed\s+for\s+season|0\s+of\s+7\s+lifts', text, re.IGNORECASE):
            data["status"] = "Closed"
        elif re.search(r'lifts?\s+open', text, re.IGNORECASE):
            open_lifts = re.search(r'(\d+)\s+of\s+\d+\s+lifts?\s+open', text, re.IGNORECASE)
            if open_lifts and int(open_lifts.group(1)) > 0:
                data["status"] = "Open"
            else:
                data["status"] = "Closed"

        data["base_depth"] = find_first([
            r'base\s*(?:depth)?[:\s]*(\d+[\d\.]*)["\s]',
            r'(\d+)["\s]+base',
        ], text, suffix='"')

        data["new_snow_24h"] = find_first([
            r'24.?hour[:\s]*(\d+[\d\.]*)["\s]',
            r'24.?hr[:\s]*(\d+[\d\.]*)["\s]',
            r'new\s+snow[:\s]*(\d+[\d\.]*)["\s]',
        ], text, suffix='"')

        data["season_total"] = find_first([
            r'season\s+total[:\s]*(\d+[\d\.]*)["\s]',
            r'(\d+)["\s]+season\s+total',
        ], text, suffix='"')

        m = re.search(r'(\d+)\s+of\s+(\d+)\s+trails?\s+open', text, re.IGNORECASE)
        if m:
            data["trails_open"] = m.group(1)
            data["trails_total"] = m.group(2)
        else:
            m = re.search(r'(\d+)\s+trails?\s+open', text, re.IGNORECASE)
            if m:
                data["trails_open"] = m.group(1)

        m = re.search(r'(\d+)\s+of\s+(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
        if m:
            data["lifts_open"] = m.group(1)
            data["lifts_total"] = m.group(2)
        else:
            m = re.search(r'(\d+)\s+lifts?\s+open', text, re.IGNORECASE)
            if m:
                data["lifts_open"] = m.group(1)

        data["surface"] = find_first([
            r'surface\s+conditions?[:\s]+([^\n\.]{5,60})',
            r'grooming[:\s]+([^\n\.]{5,60})',
        ], text)

        data["temperature"] = find_first([
            r'(\d+)\s*°\s*F',
            r'temp(?:erature)?[:\s]*(\d+)[°\s]*F',
        ], text, suffix='°F')

    except Exception as e:
        data["error"] = str(e)
        print(f"Cranmore scrape error: {e}", file=sys.stderr)

    return data


def load_existing():
    try:
        with open("conditions.json") as f:
            return json.load(f)
    except Exception:
        return {"mountains": {}}


def merge(existing, key, new_data):
    old = existing.get("mountains", {}).get(key, {})
    merged = dict(old)
    for k, v in new_data.items():
        if v not in (None, "N/A", "Unknown") or k == "error":
            merged[k] = v
        elif k not in merged:
            merged[k] = v
    return merged


if __name__ == "__main__":
    existing = load_existing()

    bm = scrape_black_mountain()
    cr = scrape_cranmore()

    conditions = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "mountains": {
            "black_mountain": merge(existing, "black_mountain", bm),
            "cranmore": merge(existing, "cranmore", cr),
        }
    }

    with open("conditions.json", "w") as f:
        json.dump(conditions, f, indent=2)

    print("conditions.json updated")
    print(json.dumps(conditions, indent=2))
