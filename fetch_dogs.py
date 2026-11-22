import json
import os
import re
from datetime import datetime, timezone
import urllib.request

RESCUEGROUPS_API_KEY = os.environ.get("RESCUEGROUPS_API_KEY", "").strip()

URGENT_KEYWORDS = re.compile(
    r"\b(urgent|euthanasia|euth|deadline|at risk|code red|rescue only|last call|plea|critical|medical|space)\b",
    re.IGNORECASE,
)

HIGH_INTAKE_CA_AREAS = {
    "fresno", "bakersfield", "kern", "riverside", "san bernardino",
    "devore", "lancaster", "palmdale", "downey", "carson", "baldwin park",
    "stockton", "modesto", "stanislaus", "sacramento", "oakland", "madera", "merced"
}


def detect_urgency_type(text: str) -> str:
    lower = text.lower()
    if "medical" in lower or "injured" in lower or "sick" in lower:
        return "Medical"
    if "behavior" in lower or "handling" in lower or "rescue only" in lower:
        return "Behavior / Rescue Only"
    if "space" in lower or "capacity" in lower or "overcrowded" in lower:
        return "Space / Deadline"
    return "Euthanasia Risk / Urgent"


def fetch_rescuegroups_ca() -> list[dict]:
    if not RESCUEGROUPS_API_KEY:
        print("Skipping RescueGroups: RESCUEGROUPS_API_KEY not set.")
        return []

    url = "https://api.rescuegroups.org/v5/public/animals/search/available/dogs?include=orgs,locations,pictures&limit=250"
    payload = {
        "data": {
            "filters": [
                {
                    "fieldName": "locations.state",
                    "operation": "equals",
                    "criteria": "CA"
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
            "User-Agent": "UrgentPawsCA/1.0"
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

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []

    for animal in body.get("data", []):
        attrs = animal.get("attributes", {})
        rels = animal.get("relationships", {})

        name = (attrs.get("name") or "").strip()
        desc = (attrs.get("descriptionText") or attrs.get("descriptionHtml") or "").strip()
        rescue_id = (attrs.get("rescueId") or animal.get("id") or "").strip()

        org_data = (rels.get("orgs", {}).get("data") or [{}])[0]
        org_attrs = orgs_by_id.get(org_data.get("id"), {})
        shelter_name = (org_attrs.get("name") or "California Shelter / Rescue").strip()

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

        listing_url = (
            attrs.get("url")
            or org_attrs.get("url")
            or f"https://www.rescuegroups.org/animals/detail?AnimalID={animal.get('id')}"
        )

        urgency_label = "Urgent / Deadline" if is_urgent_text else "High-Intake Shelter"
        needs = "Rescue Pull / Foster" if "rescue only" in combined_text.lower() else "Foster or Adopt"

        results.append({
            "id": f"rg-{animal.get('id')}",
            "animal_id": rescue_id,
            "name": name or "Unnamed Dog",
            "breed": (attrs.get("breedString") or "").strip(),
            "shelter": shelter_name,
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
            "last_checked": now_iso,
        })

    return results


def fetch_ca_shelter_scrapers() -> list[dict]:
    scraped = []
    # Modular hook for direct CA high-intake shelter feeds
    return scraped


def main():
    dogs = []
    dogs.extend(fetch_rescuegroups_ca())
    dogs.extend(fetch_ca_shelter_scrapers())

    seen = set()
    unique_dogs = []
    for dog in dogs:
        key = dog.get("listing_url") or dog.get("id")
        if key and key not in seen:
            seen.add(key)
            unique_dogs.append(dog)

    unique_dogs.sort(
        key=lambda d: (
            0 if "Urgent" in d.get("deadline_or_urgency", "") else 1,
            d.get("county", ""),
            d.get("name", ""),
        )
    )

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(unique_dogs, f, indent=2)
        f.write("\n")

    print(f"Saved {len(unique_dogs)} California dogs to dogs.json")


if __name__ == "__main__":
    main()
