import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
import urllib.parse
import urllib.request

RESCUEGROUPS_API_KEY = os.environ.get("RESCUEGROUPS_API_KEY", "").strip()
MAX_AGE_DAYS = 21

USER_AGENT = "Mozilla/5.0 (compatible; UrgentPawsCA/1.0; +https://urgentpaws.org)"

URGENT_KEYWORDS = re.compile(
    r"\b(urgent|euthanasia|euth|deadline|at risk|code red|rescue only|last call|plea|critical|time[- ]sensitive|needs foster immediately|medical urgency)\b",
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


def is_direct_dog_url(raw: str) -> str:
    """Validates that a URL is a deep link to a specific animal page, never a shelter homepage."""
    val = (raw or "").strip().rstrip(".,;)")
    if not val or val.startswith("/"):
        return ""
    if not val.startswith(("http://", "https://")):
        val = "https://" + val

    parsed = urllib.parse.urlparse(val)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")

    if "." not in host or "urgentpaws" in host:
        return ""
    if "rescuegroups.org/animals/detail" in val.lower():
        return ""
    if any(skip in host for skip in ("facebook.com", "instagram.com", "youtube.com", "tiktok.com", "google.com")):
        return ""
    # Reject bare shelter homepages (must have a subpage path or query string)
    if not path and not parsed.query:
        return ""

    return val


def resolve_direct_dog_url(attrs: dict, desc: str) -> str:
    # 1. Direct animal URL from RescueGroups attributes
    for candidate in (attrs.get("url"), attrs.get("adoptionUrl")):
        clean = is_direct_dog_url(candidate)
        if clean:
            return clean

    # 2. Direct animal deep-link embedded in description (e.g., 24Petconnect, Petango, Adopt-a-Pet, shelter animal page)
    for match in URL_IN_TEXT.findall(desc or ""):
        clean = is_direct_dog_url(match)
        if clean:
            return clean

    # No direct dog URL available — skip rather than sending users to a generic homepage
    return ""


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
        kill_date = (attrs.get("killDate") or "").strip()
        kill_reason = (attrs.get("killReason") or "").strip()
        rescue_id = (attrs.get("rescueId") or animal.get("id") or "").strip()

        # STRICT URGENCY CHECK: Must explicitly have a killDate/killReason or urgent keywords in the dog's listing
        dog_text = f"{name} {desc} {kill_reason}"
        if not (kill_date or kill_reason or URGENT_KEYWORDS.search(dog_text)):
            continue

        # STRICT DIRECT URL CHECK: Must link directly to the dog's profile, not a shelter homepage
        listing_url = resolve_direct_dog_url(attrs=attrs, desc=desc)
        if not listing_url:
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
            "shelter": shelter_name or "California Municipal Shelter",
            "city": city,
            "county": county,
            "state": state,
            "size": (attrs.get("sizeGroup") or "").strip(),
            "age": (attrs.get("ageGroup") or "").strip(),
            "urgency_type": detect_urgency_type(dog_text, kill_date),
            "deadline_or_urgency": f"Deadline {kill_date[:10]}" if kill_date else "Urgent / Deadline",
            "needs_type": needs,
            "transport_note": "",
            "photo_url": photo_url,
            "listing_url": listing_url,
            "updated_at": updated_iso,
            "last_checked": now_iso,
        })

    print(f"RescueGroups verified urgent direct-link dogs: {len(results)}")
    return results


def scrape_oakland_animal_services() -> list[dict]:
    """Scrapes OAS dog links and checks each dog's individual profile page so ONLY urgent dogs are included."""
    base_url = "https://www.oaklandanimalservices.org/adopt/dogs/"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        index_page = fetch_url(base_url)
    except Exception as exc:
        print(f"OAS index scrape error: {exc}")
        return []

    link_pattern = re.compile(
        r'href="([^"]*?/adopt/animal/\?animalId=(\d+))"',
        re.IGNORECASE,
    )

    seen_ids = set()
    candidates = []
    for match in link_pattern.finditer(index_page):
        raw_href, animal_id = match.groups()
        if animal_id in seen_ids:
            continue
        seen_ids.add(animal_id)
        listing_url = urllib.parse.urljoin(base_url, html.unescape(raw_href))
        candidates.append((animal_id, listing_url))

    results = []
    for animal_id, listing_url in candidates:
        try:
            detail_html = fetch_url(listing_url)
        except Exception:
            continue

        # Strip scripts/styles and HTML tags to inspect the dog's actual profile bio
        cleaned_html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", detail_html, flags=re.IGNORECASE | re.DOTALL)
        plain_text = re.sub(r"<[^>]+>", " ", cleaned_html)
        plain_text = re.sub(r"\s+", " ", html.unescape(plain_text)).strip()

        # Skip dogs whose profile does NOT explicitly state an urgent/deadline/medical/rescue-only plea
        if not URGENT_KEYWORDS.search(plain_text):
            continue

        title_match = re.search(r"<h1[^>]*>(.*?)</h1>", detail_html, re.IGNORECASE | re.DOTALL)
        raw_name = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""
        name = html.unescape(raw_name).split("|")[0].strip() or f"OAS #{animal_id}"

        og_img = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', detail_html, re.IGNORECASE)
        photo_url = html.unescape(og_img.group(1)) if og_img else ""

        needs = "Rescue Pull / Foster" if "rescue only" in plain_text.lower() else "Foster or Adopt"

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
            "urgency_type": detect_urgency_type(plain_text),
            "deadline_or_urgency": "Urgent / Deadline",
            "needs_type": needs,
            "transport_note": "",
            "photo_url": photo_url,
            "listing_url": listing_url,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"Oakland Animal Services verified urgent dogs: {len(results)}")
    return results


def main():
    dogs = []
    dogs.extend(scrape_oakland_animal_services())
    dogs.extend(fetch_rescuegroups_shelters_ca())

    seen = set()
    unique_dogs = []
    for dog in dogs:
        key = dog.get("listing_url") or dog.get("id")
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

    print(f"Total verified urgent direct-link shelter dogs saved: {len(unique_dogs)}")


if __name__ == "__main__":
    main()
