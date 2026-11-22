import json
import os
import re
from datetime import datetime, timedelta, timezone
import urllib.parse
import urllib.request

RESCUEGROUPS_API_KEY = os.environ.get("RESCUEGROUPS_API_KEY", "").strip()
MAX_AGE_DAYS = 30

USER_AGENT = "Mozilla/5.0 (compatible; UrgentPawsCA/1.0; +https://urgentpaws.org)"

URGENT_KEYWORDS = re.compile(
    r"\b(urgent|euthanasia|euth|deadline|at risk|code red|rescue only|last call|plea|critical|time[- ]sensitive|medical|overcrowded|space|long[- ]stay)\b",
    re.IGNORECASE,
)

# Includes municipal shelters and county-contracted humane societies/SPCAs (e.g., San Diego Humane, Marin Humane)
SHELTER_NAME_HINTS = re.compile(
    r"\b(animal services|animal control|animal care|county|municipal|city of|shelter|department of animal|acc|seaaca|laas|oc animal|humane society|spca)\b",
    re.IGNORECASE,
)

RESCUE_EXCLUDE_HINTS = re.compile(
    r"\b(rescue|sanctuary|foster network|foundation|league|paws|hearts)\b",
    re.IGNORECASE,
)

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


def clean_valid_url(raw: str) -> str:
    val = (raw or "").strip().rstrip(".,;)")
    if not val or val.startswith("/"):
        return ""
    if not val.startswith(("http://", "https://")):
        val = "https://" + val

    parsed = urllib.parse.urlparse(val)
    host = (parsed.netloc or "").lower()
    if "." not in host or "urgentpaws" in host:
        return ""
    if "rescuegroups.org/animals/detail" in val.lower():
        return ""
    if any(skip in host for skip in ("facebook.com", "instagram.com", "youtube.com", "tiktok.com", "google.com")):
        return ""
    return val


def is_deep_dog_link(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return bool((parsed.path or "").strip("/") or parsed.query)


def resolve_listing_url(attrs: dict, org_attrs: dict, desc: str) -> tuple[str, bool]:
    for cand in (attrs.get("url"), attrs.get("adoptionUrl")):
        url = clean_valid_url(cand)
        if url and is_deep_dog_link(url):
            return url, True

    for match in URL_IN_TEXT.findall(desc or ""):
        url = clean_valid_url(match)
        if url and is_deep_dog_link(url):
            return url, True

    for cand in (org_attrs.get("adoptionUrl"), org_attrs.get("url")):
        url = clean_valid_url(cand)
        if url:
            return url, False

    return "", False


def detect_urgency_type(text: str, kill_date: str = "") -> str:
    lower = text.lower()
    if kill_date:
        return f"Deadline ({kill_date[:10]})"
    if "medical" in lower or "injured" in lower or "sick" in lower:
        return "Medical Urgent"
    if "behavior" in lower or "handling" in lower or "rescue only" in lower:
        return "Behavior / Rescue Only"
    if "space" in lower or "capacity" in lower or "overcrowded" in lower:
        return "Space / Capacity Deadline"
    return "Urgent / Deadline"


def fetch_rescuegroups_shelters_ca() -> list[dict]:
    if not RESCUEGROUPS_API_KEY:
        print("ERROR: RESCUEGROUPS_API_KEY secret is empty.")
        return []

    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(days=MAX_AGE_DAYS)

    raw_animals = []
    orgs_by_id = {}
    locations_by_id = {}
    pics_by_id = {}

    for page_num in range(1, 5):
        url = f"https://api.rescuegroups.org/v5/public/animals/search/available/dogs?include=orgs,locations,pictures&limit=250&page={page_num}"
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
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"RescueGroups API page {page_num} error: {exc}")
            break

        batch = body.get("data", [])
        print(f"Page {page_num}: fetched {len(batch)} dogs")
        raw_animals.extend(batch)

        for item in body.get("included", []):
            t = item.get("type")
            i = item.get("id")
            a = item.get("attributes", {})
            if t == "orgs":
                orgs_by_id[i] = a
            elif t == "locations":
                locations_by_id[i] = a
            elif t == "pictures":
                pics_by_id[i] = a

        if len(batch) < 250:
            break

    print(f"Total CA dogs scanned: {len(raw_animals)} | Total orgs included: {len(orgs_by_id)}")

    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []
    stale_count = 0
    non_shelter_count = 0
    non_urgent_count = 0
    no_url_count = 0
    matched_shelters = set()

    for animal in raw_animals:
        attrs = animal.get("attributes", {})
        rels = animal.get("relationships", {})

        updated_dt = parse_iso_date(attrs.get("updatedDate") or attrs.get("createdDate") or "")
        if updated_dt and updated_dt < cutoff_utc:
            stale_count += 1
            continue

        org_data = (rels.get("orgs", {}).get("data") or [{}])[0]
        org_attrs = orgs_by_id.get(org_data.get("id"), {})
        shelter_name = (org_attrs.get("name") or "").strip()
        org_type = (org_attrs.get("type") or "").strip().lower()

        is_shelter = (org_type == "shelter") or bool(SHELTER_NAME_HINTS.search(shelter_name))
        is_rescue = bool(RESCUE_EXCLUDE_HINTS.search(shelter_name)) and not bool(SHELTER_NAME_HINTS.search(shelter_name))
        if not is_shelter or is_rescue:
            non_shelter_count += 1
            continue

        matched_shelters.add(shelter_name)

        name = (attrs.get("name") or "").strip()
        desc = (attrs.get("descriptionText") or attrs.get("descriptionHtml") or "").strip()
        kill_date = (attrs.get("killDate") or "").strip()
        kill_reason = (attrs.get("killReason") or "").strip()
        rescue_id = (attrs.get("rescueId") or animal.get("id") or "").strip()

        dog_text = f"{name} {desc} {kill_reason}"
        if not (kill_date or kill_reason or URGENT_KEYWORDS.search(dog_text)):
            non_urgent_count += 1
            continue

        listing_url, is_direct = resolve_listing_url(attrs=attrs, org_attrs=org_attrs, desc=desc)
        if not listing_url:
            no_url_count += 1
            continue

        loc_data = (rels.get("locations", {}).get("data") or [{}])[0]
        loc_attrs = locations_by_id.get(loc_data.get("id"), {})
        city = (loc_attrs.get("city") or org_attrs.get("city") or "").strip()
        county = (loc_attrs.get("county") or "").strip()
        state = (loc_attrs.get("state") or org_attrs.get("state") or "CA").strip()

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

        needs = "Rescue Pull / Foster" if "rescue only" in dog_text.lower() else "Foster or Adopt"
        updated_iso = updated_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if updated_dt else now_iso

        results.append({
            "id": f"rg-shelter-{animal.get('id')}",
            "animal_id": rescue_id,
            "name": name or "Unnamed Dog",
            "breed": (attrs.get("breedString") or "").strip(),
            "shelter": shelter_name,
            "city": city,
            "county": county,
            "state": state,
            "size": (attrs.get("sizeGroup") or "").strip(),
            "age": (attrs.get("ageGroup") or "").strip(),
            "urgency_type": detect_urgency_type(dog_text, kill_date),
            "deadline_or_urgency": f"Deadline {kill_date[:10]}" if kill_date else "Urgent / Deadline",
            "needs_type": needs,
            "transport_note": "" if is_direct else f"Use ID {rescue_id} on shelter portal",
            "photo_url": photo_url,
            "listing_url": listing_url,
            "updated_at": updated_iso,
            "last_checked": now_iso,
        })

    print(f"Matched shelters: {sorted(matched_shelters)}")
    print(
        f"Saved: {len(results)} | Dropped stale (>30d): {stale_count} | "
        f"Dropped non-shelter: {non_shelter_count} | Dropped non-urgent: {non_urgent_count} | "
        f"Dropped no URL: {no_url_count}"
    )
    return results


def main():
    dogs = fetch_rescuegroups_shelters_ca()

    seen = set()
    unique_dogs = []
    for dog in dogs:
        key = dog.get("id")
        if key and key not in seen:
            seen.add(key)
            unique_dogs.append(dog)

    unique_dogs.sort(
        key=lambda d: (
            -(parse_iso_date(d.get("updated_at") or "").timestamp() if parse_iso_date(d.get("updated_at") or "") else 0),
            d.get("county", ""),
            d.get("name", ""),
        )
    )

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(unique_dogs, f, indent=2)
        f.write("\n")

    print(f"Total urgent shelter dogs written to dogs.json: {len(unique_dogs)}")


if __name__ == "__main__":
    main()
