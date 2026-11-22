import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
import urllib.parse
import urllib.request

RESCUEGROUPS_API_KEY = os.environ.get("RESCUEGROUPS_API_KEY", "").strip()
MAX_AGE_DAYS = 21  # Drop RescueGroups listings not updated within this many days

USER_AGENT = "Mozilla/5.0 (compatible; UrgentPawsCA/1.0; +https://urgentpaws.org)"

URGENT_KEYWORDS = re.compile(
    r"\b(urgent|euthanasia|euth|deadline|at risk|code red|rescue only|last call|plea|critical|medical|space|overcrowded)\b",
    re.IGNORECASE,
)

SHELTER_NAME_HINTS = re.compile(
    r"\b(animal services|animal control|county|municipal|city of|shelter|department of animal|acc)\b",
    re.IGNORECASE,
)

RESCUE_EXCLUDE_HINTS = re.compile(
    r"\b(rescue|sanctuary|foster network|foundation|league)\b",
    re.IGNORECASE,
)

HIGH_INTAKE_CA_AREAS = {
    "fresno", "bakersfield", "kern", "riverside", "san bernardino",
    "devore", "lancaster", "palmdale", "downey", "carson", "baldwin park",
    "stockton", "modesto", "stanislaus", "sacramento", "oakland", "madera",
    "merced", "visalia", "tulare", "san jose", "contra costa", "hayward"
}

URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)


def parse_iso_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        cleaned = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_url(raw: str) -> str:
    val = (raw or "").strip().rstrip(".,;)")
    if not val:
        return ""
    if not val.startswith(("http://", "https://")):
        val = "https://" + val
    return val


def resolve_listing_url(attrs: dict, org_attrs: dict, desc: str, shelter_name: str, animal_id: str, dog_name: str) -> str:
    for candidate in (attrs.get("url"), attrs.get("adoptionUrl")):
        clean = normalize_url(candidate)
        if clean and "rescuegroups.org/animals/detail" not in clean:
            return clean

    for match in URL_IN_TEXT.findall(desc or ""):
        clean = normalize_url(match)
        if clean and not any(skip in clean.lower() for skip in ("facebook.com", "instagram.com", "youtube.com")):
            return clean

    for candidate in (org_attrs.get("adoptionUrl"), org_attrs.get("url")):
        clean = normalize_url(candidate)
        if clean:
            return clean

    query = " ".join(part for part in (shelter_name, animal_id, dog_name) if part)
    return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"


def detect_urgency_type(text: str) -> str:
    lower = text.lower()
    if "medical" in lower or "injured" in lower or "sick" in lower:
        return "Medical"
    if "behavior" in lower or "handling" in lower or "rescue only" in lower:
        return "Behavior / Rescue Only"
    if "space" in lower or "capacity" in lower or "overcrowded" in lower:
        return "Space / Deadline"
    return "Shelter Capacity / Urgent"


def fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_rescuegroups_shelters_ca() -> list[dict]:
    if not RESCUEGROUPS_API_KEY:
        print("Skipping RescueGroups: RESCUEGROUPS_API_KEY not set.")
        return []

    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(days=MAX_AGE_DAYS)
    cutoff_api_str = cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = (
        "https://api.rescuegroups.org/v5/public/animals/search/available/dogs"
        "?include=orgs,locations,pictures&sort=-animals.updatedDate&limit=250"
    )
    payload = {
        "data": {
            "filters": [
                {
                    "fieldName": "locations.state",
                    "operation": "equals",
                    "criteria": "CA"
                },
                {
                    "fieldName": "orgs.type",
                    "operation": "equals",
                    "criteria": "Shelter"
                },
                {
                    "fieldName": "animals.updatedDate",
                    "operation": "greaterthan",
                    "criteria": cutoff_api_str
                }
            ]
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/vnd.api+json",
            "Authorization": RESCUEGROUPS_API_KEY,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"RescueGroups API error: {exc}")
        return []

    included = body.get("included", [])
    orgs_by_id = {}
    locations_by_id = {}
    pics_by_id = {}

    for item in included:
        item_type = item.get("type")
        item_id = item.get("id")
        attrs = item.get("attributes", {})
        if item_type == "orgs":
            orgs_by_id[item_id] = attrs
        elif item_type == "locations":
            locations_by_id[item_id] = attrs
        elif item_type == "pictures":
            pics_by_id[item_id] = attrs

    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []

    for animal in body.get("data", []):
        attrs = animal.get("attributes", {})
        rels = animal.get("relationships", {})

        # Enforce strict freshness check on updatedDate / createdDate
        updated_dt = parse_iso_date(attrs.get("updatedDate") or attrs.get("createdDate") or "")
        if updated_dt and updated_dt < cutoff_utc:
            continue

        org_data = (rels.get("orgs", {}).get("data") or [{}])[0]
        org_attrs = orgs_by_id.get(org_data.get("id"), {})
        shelter_name = (org_attrs.get("name") or "").strip()
        org_type = (org_attrs.get("type") or "").strip().lower()

        if org_type and org_type != "shelter":
            continue
        if RESCUE_EXCLUDE_HINTS.search(shelter_name) and not SHELTER_NAME_HINTS.search(shelter_name):
            continue

        name = (attrs.get("name") or "").strip()
        desc = (attrs.get("descriptionText") or attrs.get("descriptionHtml") or "").strip()
        rescue_id = (attrs.get("rescueId") or animal.get("id") or "").strip()

        loc_data = (rels.get("locations", {}).get("data") or [{}])[0]
        loc_attrs = locations_by_id.get(loc_data.get("id"), {})
        city = (loc_attrs.get("city") or org_attrs.get("city") or "").strip()
        county = (loc_attrs.get("county") or "").strip()
        state = (loc_attrs.get("state") or org_attrs.get("state") or "CA").strip()

        combined_text = f"{name} {desc} {shelter_name} {city} {county}"
        is_urgent_text = bool(URGENT_KEYWORDS.search(combined_text))
        is_high_intake_area = any(area in combined_text.lower() for area in HIGH_INTAKE_CA_AREAS)

        if not (is_urgent_text or is_high_intake_area):
            continue

        pic_list = rels.get("pictures", {}).get("data") or []
        photo_url = ""
        if pic_list:
            pic_attrs = pics_by_id.get(pic_list[0].get("id"), {})
            photo_url = (
                pic_attrs.get("large", {}).get("url")
                or pic_attrs.get("original", {}).get("url")
                or pic_attrs.get("small", {}).get("url")
                or ""
            )

        listing_url = resolve_listing_url(
            attrs=attrs,
            org_attrs=org_attrs,
            desc=desc,
            shelter_name=shelter_name,
            animal_id=rescue_id,
            dog_name=name,
        )

        urgency_label = "Urgent / Deadline" if is_urgent_text else "Municipal Shelter"
        needs = "Rescue Pull / Foster" if "rescue only" in combined_text.lower() else "Foster or Adopt"
        updated_iso = updated_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if updated_dt else now_iso

        results.append({
            "id": f"rg-shelter-{animal.get('id')}",
            "animal_id": rescue_id,
            "name": name or "Unnamed Dog",
            "breed": (attrs.get("breedString") or "").strip(),
            "shelter": shelter_name or "California Municipal Shelter",
            "city": city,
            "county": county,
            "state": state,
            "size": (attrs.get("sizeGroup") or "").strip(),
            "age": (attrs.get("ageGroup") or "").strip(),
            "urgency_type": detect_urgency_type(combined_text),
            "deadline_or_urgency": urgency_label,
            "needs_type": needs,
            "transport_note": "",
            "photo_url": photo_url,
            "listing_url": listing_url,
            "updated_at": updated_iso,
            "last_checked": now_iso,
        })

    print(f"RescueGroups fresh shelters returned: {len(results)}")
    return results


def scrape_oakland_animal_services() -> list[dict]:
    url = "https://www.oaklandanimalservices.org/adopt/dogs/"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        page = fetch_url(url)
    except Exception as exc:
        print(f"OAS scrape error: {exc}")
        return []

    results = []
    card_pattern = re.compile(
        r'<a[^>]+href="(https://www\.oaklandanimalservices\.org/adopt/animal/\?animalId=(\d+))"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    seen_ids = set()
    for match in card_pattern.finditer(page):
        listing_url, animal_id, inner_html = match.groups()
        if animal_id in seen_ids:
            continue
        seen_ids.add(animal_id)

        img_match = re.search(r'<img[^>]+src="([^"]+)"', inner_html, re.IGNORECASE)
        photo_url = html.unescape(img_match.group(1)) if img_match else ""

        clean_text = re.sub(r"<[^>]+>", " ", inner_html)
        clean_text = re.sub(r"\s+", " ", html.unescape(clean_text)).strip()
        name = clean_text.split(" ")[0] if clean_text else f"OAS #{animal_id}"

        results.append({
            "id": f"oas-{animal_id}",
            "animal_id": f"A{animal_id}",
            "name": name,
            "breed": "",
            "shelter": "Oakland Animal Services",
            "city": "Oakland",
            "county": "Alameda",
            "state": "CA",
            "size": "",
            "age": "",
            "urgency_type": "High-Intake Municipal Shelter",
            "deadline_or_urgency": "Urgent / Capacity",
            "needs_type": "Foster or Adopt",
            "transport_note": "",
            "photo_url": photo_url,
            "listing_url": listing_url,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"Oakland Animal Services scraped: {len(results)}")
    return results


def main():
    dogs = []
    dogs.extend(scrape_oakland_animal_services())
    dogs.extend(fetch_rescuegroups_shelters_ca())

    seen = set()
    unique_dogs = []
    for dog in dogs:
        key = dog.get("id") or dog.get("listing_url")
        if key and key not in seen:
            seen.add(key)
            unique_dogs.append(dog)

    # Sort urgent deadlines first, then most recently updated first
    unique_dogs.sort(
        key=lambda d: (
            0 if "Urgent" in d.get("deadline_or_urgency", "") else 1,
            -(parse_iso_date(d.get("updated_at") or "").timestamp() if parse_iso_date(d.get("updated_at") or "") else 0),
        )
    )

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(unique_dogs, f, indent=2)
        f.write("\n")

    print(f"Total fresh municipal shelter dogs saved: {len(unique_dogs)}")


if __name__ == "__main__":
    main()
