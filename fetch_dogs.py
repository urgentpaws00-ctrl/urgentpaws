import html
import json
import re
from datetime import datetime, timezone
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

URGENT_KEYWORDS = re.compile(
    r"\b(urgent|euthanasia|euth|deadline|at risk|code red|rescue only|last call|plea|critical|time[- ]sensitive|medical|overcrowded|space|long[- ]stay|emergency|foster needed immediately)\b",
    re.IGNORECASE,
)

# High-intake California municipal shelters on 24Petconnect
PETCONNECT_SHELTERS = [
    {"code": "LACO", "name": "LA County Animal Care & Control", "city": "Downey", "county": "Los Angeles"},
    {"code": "LACO1", "name": "LA County ACC - Downey", "city": "Downey", "county": "Los Angeles"},
    {"code": "LACO2", "name": "LA County ACC - Carson/Gardena", "city": "Gardena", "county": "Los Angeles"},
    {"code": "LACO3", "name": "LA County ACC - Baldwin Park", "city": "Baldwin Park", "county": "Los Angeles"},
    {"code": "LACO4", "name": "LA County ACC - Lancaster", "city": "Lancaster", "county": "Los Angeles"},
    {"code": "LACO5", "name": "LA County ACC - Palmdale", "city": "Palmdale", "county": "Los Angeles"},
    {"code": "LACO6", "name": "LA County ACC - Castaic", "city": "Castaic", "county": "Los Angeles"},
    {"code": "RVSD", "name": "Riverside County Dept of Animal Services", "city": "Jurupa Valley", "county": "Riverside"},
    {"code": "RVSD1", "name": "Riverside County - Coachella Valley", "city": "Thousand Palms", "county": "Riverside"},
    {"code": "SBCO", "name": "San Bernardino County - Devore", "city": "San Bernardino", "county": "San Bernardino"},
    {"code": "SBCO1", "name": "San Bernardino County - Big Bear", "city": "Big Bear", "county": "San Bernardino"},
    {"code": "KERN", "name": "Kern County Animal Services", "city": "Bakersfield", "county": "Kern"},
    {"code": "STKN", "name": "Stockton Animal Services", "city": "Stockton", "county": "San Joaquin"},
]

BAY_AREA_SHELTERLUV_PAGES = [
    {
        "page_url": "https://www.oaklandanimalservices.org/adopt/dogs/",
        "shelter": "Oakland Animal Services",
        "city": "Oakland",
        "county": "Alameda",
        "fallback_gid": "1653",
    },
    {
        "page_url": "https://www.sfanimalcare.org/adoptable-animals/dogs/",
        "shelter": "SF Animal Care & Control",
        "city": "San Francisco",
        "county": "San Francisco",
        "fallback_gid": "1889",
    },
]


def http_get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_post_json(url: str, payload: dict, timeout: int = 25) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def detect_urgency_type(text: str, days_in_shelter: int = 0) -> str:
    lower = text.lower()
    if "medical" in lower or "injured" in lower or "sick" in lower:
        return "Medical Urgent"
    if "behavior" in lower or "handling" in lower or "rescue only" in lower:
        return "Behavior / Rescue Only"
    if "space" in lower or "capacity" in lower or "overcrowded" in lower or "deadline" in lower or "euth" in lower:
        return "Space / Deadline"
    if days_in_shelter >= 30:
        return f"Long-Stay At Risk ({days_in_shelter}d)"
    return "High-Intake Shelter"


def days_since(date_str: str) -> int:
    if not date_str:
        return 0
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(date_str[:19], fmt).replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - dt).days)
        except ValueError:
            continue
    return 0


# ---------------------------------------------------------------------------
# PIPELINE 1: 24Petconnect (CA Municipal & County High-Intake Shelters)
# ---------------------------------------------------------------------------
def fetch_24petconnect_shelters() -> list[dict]:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []

    for shelter in PETCONNECT_SHELTERS:
        code = shelter["code"]
        search_url = f"https://24petconnect.com/{code}AdoptablePets?at=DOG"
        try:
            page_html = http_get(search_url)
        except Exception:
            continue

        # Extract animal IDs and basic card details from 24Petconnect listing cards
        card_matches = re.findall(
            r'DetailsMain/' + re.escape(code) + r'/([A-Z0-9-]+)',
            page_html,
            re.IGNORECASE,
        )
        seen_ids = set()
        for animal_id in card_matches:
            animal_id = animal_id.upper()
            if animal_id in seen_ids:
                continue
            seen_ids.add(animal_id)

            detail_url = f"https://24petconnect.com/DetailsMain/{code}/{animal_id}"
            # Grab surrounding HTML snippet around this animal ID to inspect name, photo, and urgent notes
            idx = page_html.upper().find(f"DETAILSMAIN/{code}/{animal_id}")
            snippet = page_html[max(0, idx - 600): idx + 1200] if idx != -1 else ""
            plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", snippet))).strip()

            img_match = re.search(r'<img[^>]+src="([^"]+)"', snippet, re.IGNORECASE)
            photo_url = urllib.parse.urljoin("https://24petconnect.com/", html.unescape(img_match.group(1))) if img_match else ""

            # Extract dog name if present in alt text or heading
            name_match = re.search(r'alt="([^"]+)"', snippet, re.IGNORECASE)
            raw_name = html.unescape(name_match.group(1)).strip() if name_match else ""
            name = raw_name if raw_name and len(raw_name) < 35 and "logo" not in raw_name.lower() else f"Dog #{animal_id}"

            is_urgent = bool(URGENT_KEYWORDS.search(plain))
            needs = "Rescue Pull / Foster" if "rescue only" in plain.lower() else "Foster or Adopt"

            results.append({
                "id": f"24pc-{code.lower()}-{animal_id}",
                "animal_id": animal_id,
                "name": name,
                "breed": "",
                "shelter": shelter["name"],
                "city": shelter["city"],
                "county": shelter["county"],
                "state": "CA",
                "size": "",
                "age": "",
                "urgency_type": detect_urgency_type(plain),
                "deadline_or_urgency": "Urgent / Deadline" if is_urgent else "High-Intake County Shelter",
                "needs_type": needs,
                "transport_note": "",
                "photo_url": photo_url,
                "listing_url": detail_url,
                "updated_at": now_iso,
                "last_checked": now_iso,
            })

    print(f"[1/3] 24Petconnect county shelter dogs scraped: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# PIPELINE 2: Bay Area Municipal Shelters via Shelterluv JSON Feeds
# ---------------------------------------------------------------------------
def fetch_bay_area_shelterluv() -> list[dict]:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []

    for cfg in BAY_AREA_SHELTERLUV_PAGES:
        gid = cfg["fallback_gid"]
        try:
            page_html = http_get(cfg["page_url"])
            gid_match = re.search(r'gid["\s:=]+(\d{3,6})', page_html)
            if gid_match:
                gid = gid_match.group(1)
        except Exception:
            pass

        api_url = f"https://www.shelterluv.com/api/v3/available-animals/{gid}"
        try:
            data = json.loads(http_get(api_url))
        except Exception as exc:
            print(f"Shelterluv feed error for {cfg['shelter']} (gid={gid}): {exc}")
            continue

        animals = data.get("animals", []) if isinstance(data, dict) else []
        for a in animals:
            species = (a.get("species") or a.get("type") or "").lower()
            if species and "dog" not in species:
                continue

            nid = str(a.get("nid") or a.get("id") or "").strip()
            internal_id = str(a.get("internal_id") or nid).strip()
            name = (a.get("name") or "").strip() or f"Dog #{internal_id}"
            breed = (a.get("breed") or "").strip()
            age = (a.get("age_group") or a.get("age") or "").strip()
            size = (a.get("size") or "").strip()
            desc = (a.get("description") or "").strip()
            photo_url = (a.get("cover_photo") or a.get("photo") or "").strip()

            intake_raw = str(a.get("intake_date") or a.get("updated_at") or "")
            stay_days = days_since(intake_raw)
            combined = f"{name} {desc}"
            is_urgent = bool(URGENT_KEYWORDS.search(combined)) or stay_days >= 30

            # Only include urgent, medical, rescue-only, or 30+ day long-stay dogs from Bay Area municipal shelters
            if not is_urgent:
                continue

            direct_url = (a.get("public_url") or a.get("url") or "").strip()
            if not direct_url:
                if "oakland" in cfg["page_url"] and nid:
                    direct_url = f"https://www.oaklandanimalservices.org/adopt/animal/?animalId={nid}"
                elif nid:
                    direct_url = f"https://www.shelterluv.com/embedjs/animal/{nid}"
                else:
                    direct_url = cfg["page_url"]

            needs = "Rescue Pull / Foster" if "rescue only" in combined.lower() else "Foster or Adopt"

            results.append({
                "id": f"sl-{gid}-{nid or internal_id}",
                "animal_id": internal_id,
                "name": name,
                "breed": breed,
                "shelter": cfg["shelter"],
                "city": cfg["city"],
                "county": cfg["county"],
                "state": "CA",
                "size": size,
                "age": age,
                "urgency_type": detect_urgency_type(combined, stay_days),
                "deadline_or_urgency": "Urgent / Deadline" if URGENT_KEYWORDS.search(combined) else f"Long-Stay ({stay_days}d)",
                "needs_type": needs,
                "transport_note": "",
                "photo_url": photo_url,
                "listing_url": direct_url,
                "updated_at": now_iso,
                "last_checked": now_iso,
            })

    print(f"[2/3] Bay Area Shelterluv urgent/long-stay dogs: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# PIPELINE 3: Dedicated Shelter Urgent / Plea Pages (Fresno & Sacramento)
# ---------------------------------------------------------------------------
def fetch_dedicated_urgent_pages() -> list[dict]:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []

    # Fresno Animal Center (public Petango / shelter urgent & adoptable dogs)
    fresno_url = "https://www.fresno.gov/mayor/animal-center/"
    try:
        page_html = http_get(fresno_url)
        petango_links = re.findall(
            r'href="(https?://[^"]*petango\.com/Adopt/Dog-Details\.aspx\?id=(\d+)[^"]*)"',
            page_html,
            re.IGNORECASE,
        )
        seen = set()
        for link, pid in petango_links:
            if pid in seen:
                continue
            seen.add(pid)
            results.append({
                "id": f"fresno-{pid}",
                "animal_id": f"A{pid}",
                "name": f"Fresno Dog #{pid}",
                "breed": "",
                "shelter": "Fresno Animal Center",
                "city": "Fresno",
                "county": "Fresno",
                "state": "CA",
                "size": "",
                "age": "",
                "urgency_type": "High-Intake Municipal Shelter",
                "deadline_or_urgency": "Urgent / Capacity",
                "needs_type": "Foster or Adopt",
                "transport_note": "",
                "photo_url": "",
                "listing_url": html.unescape(link),
                "updated_at": now_iso,
                "last_checked": now_iso,
            })
    except Exception as exc:
        print(f"Fresno check skipped: {exc}")

    # Sacramento County Bradshaw Animal Shelter
    sac_url = "https://animalcare.saccounty.gov/Adopt/Pages/ViewAllAdoptableAnimals.aspx"
    try:
        page_html = http_get(sac_url)
        sac_links = re.findall(
            r'href="([^"]*AnimalDetail[^"]*id=([A-Z0-9]+)[^"]*)"',
            page_html,
            re.IGNORECASE,
        )
        seen = set()
        for raw_link, aid in sac_links:
            if aid in seen:
                continue
            seen.add(aid)
            full_url = urllib.parse.urljoin(sac_url, html.unescape(raw_link))
            results.append({
                "id": f"saccounty-{aid}",
                "animal_id": aid,
                "name": f"Bradshaw Dog #{aid}",
                "breed": "",
                "shelter": "Sacramento County Bradshaw Shelter",
                "city": "Sacramento",
                "county": "Sacramento",
                "state": "CA",
                "size": "",
                "age": "",
                "urgency_type": "High-Intake County Shelter",
                "deadline_or_urgency": "Municipal Shelter",
                "needs_type": "Foster or Adopt",
                "transport_note": "",
                "photo_url": "",
                "listing_url": full_url,
                "updated_at": now_iso,
                "last_checked": now_iso,
            })
    except Exception as exc:
        print(f"Sacramento check skipped: {exc}")

    print(f"[3/3] Dedicated urgent pages scraped: {len(results)}")
    return results


def main():
    dogs = []
    dogs.extend(fetch_24petconnect_shelters())
    dogs.extend(fetch_bay_area_shelterluv())
    dogs.extend(fetch_dedicated_urgent_pages())

    seen = set()
    unique_dogs = []
    for dog in dogs:
        key = dog.get("listing_url") or dog.get("id")
        if key and key not in seen:
            seen.add(key)
            unique_dogs.append(dog)

    unique_dogs.sort(
        key=lambda d: (
            0 if "Urgent" in d.get("deadline_or_urgency", "") or "Deadline" in d.get("deadline_or_urgency", "") else 1,
            d.get("county", ""),
            d.get("name", ""),
        )
    )

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(unique_dogs, f, indent=2)
        f.write("\n")

    print(f"Total direct-shelter dogs saved to dogs.json: {len(unique_dogs)}")


if __name__ == "__main__":
    main()
