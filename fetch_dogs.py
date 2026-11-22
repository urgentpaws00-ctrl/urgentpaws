import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
import urllib.parse
import urllib.request

RESCUEGROUPS_API_KEY = os.environ.get("RESCUEGROUPS_API_KEY", "").strip()
MAX_AGE_DAYS = 21

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

URGENT_KEYWORDS = re.compile(
    r"\b(urgent|euthanasia|euth|deadline|at risk|code red|rescue only|last call|plea|critical|time[- ]sensitive|medical|overcrowded|space|long[- ]stay|senior)\b",
    re.IGNORECASE,
)

SHELTER_NAME_HINTS = re.compile(
    r"\b(animal services|animal control|county|municipal|city of|shelter|department of animal|acc|humane society)\b",
    re.IGNORECASE,
)

RESCUE_EXCLUDE_HINTS = re.compile(
    r"\b(rescue|sanctuary|foster network|foundation|league)\b",
    re.IGNORECASE,
)

URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


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
    if not path and not parsed.query:
        return ""

    return val


def scrape_oakland_animal_services() -> list[dict]:
    """Direct municipal shelter feed: Oakland Animal Services (direct per-dog URLs)."""
    base_url = "https://www.oaklandanimalservices.org/adopt/dogs/"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        page = fetch_html(base_url)
    except Exception as exc:
        print(f"OAS error: {exc}")
        return []

    card_pattern = re.compile(
        r'<a[^>]+href="([^"]*?/adopt/animal/\?animalId=(\d+))"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    seen_ids = set()
    results = []
    for match in card_pattern.finditer(page):
        raw_href, animal_id, inner_html = match.groups()
        if animal_id in seen_ids:
            continue
        seen_ids.add(animal_id)

        listing_url = urllib.parse.urljoin(base_url, html.unescape(raw_href))
        img_match = re.search(r'<img[^>]+src="([^"]+)"', inner_html, re.IGNORECASE)
        photo_url = urllib.parse.urljoin(base_url, html.unescape(img_match.group(1))) if img_match else ""

        clean_text = re.sub(r"<[^>]+>", " ", inner_html)
        clean_text = re.sub(r"\s+", " ", html.unescape(clean_text)).strip()
        name = clean_text.split(" ")[0] if clean_text else f"OAS #{animal_id}"

        results.append({
            "id": f"oas-{animal_id}",
            "animal_id": f"A{animal_id}",
            "name": name,
            "breed": "",
            "shelter": "Oakland Animal Services (Municipal)",
            "city": "Oakland",
            "county": "Alameda",
            "state": "CA",
            "size": "",
            "age": "",
            "urgency_type": "High-Intake Municipal Shelter",
            "deadline_or_urgency": "Municipal Shelter",
            "needs_type": "Foster or Adopt",
            "transport_note": "",
            "photo_url": photo_url,
            "listing_url": listing_url,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"Oakland Animal Services scraped: {len(results)}")
    return results


def scrape_sf_acc() -> list[dict]:
    """Direct municipal shelter feed: San Francisco Animal Care & Control."""
    base_url = "https://www.sfanimalcare.org/adoptable-animals/dogs/"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        page = fetch_html(base_url)
    except Exception as exc:
        print(f"SFACC error: {exc}")
        return []

    link_pattern = re.compile(
        r'<a[^>]+href="(https?://www\.sfanimalcare\.org/adoptable-animals/dogs/[^"/]+/?)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    seen_urls = set()
    results = []
    for match in link_pattern.finditer(page):
        listing_url, inner_html = match.groups()
        if listing_url in seen_urls or listing_url.rstrip("/") == base_url.rstrip("/"):
            continue
        seen_urls.add(listing_url)

        slug = listing_url.rstrip("/").split("/")[-1]
        clean_text = re.sub(r"<[^>]+>", " ", inner_html)
        clean_text = re.sub(r"\s+", " ", html.unescape(clean_text)).strip()
        name = clean_text or slug.replace("-", " ").title()

        img_match = re.search(r'<img[^>]+src="([^"]+)"', inner_html, re.IGNORECASE)
        photo_url = html.unescape(img_match.group(1)) if img_match else ""

        results.append({
            "id": f"sfacc-{slug}",
            "animal_id": slug.upper(),
            "name": name,
            "breed": "",
            "shelter": "SF Animal Care & Control (Municipal)",
            "city": "San Francisco",
            "county": "San Francisco",
            "state": "CA",
            "size": "",
            "age": "",
            "urgency_type": "Municipal Shelter",
            "deadline_or_urgency": "Municipal Shelter",
            "needs_type": "Foster or Adopt",
            "transport_note": "",
            "photo_url": photo_url,
            "listing_url": listing_url,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"SF Animal Care & Control scraped: {len(results)}")
    return results


def fetch_rescuegroups_shelters_ca() -> list[dict]:
    """Pulls recent CA municipal shelter listings from RescueGroups that have direct dog URLs."""
    if not RESCUEGROUPS_API_KEY:
        return []

    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(days=MAX_AGE_DAYS)

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
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"RescueGroups error: {exc}")
        return []

    orgs_by_id = {}
    locations_by_id = {}
    pics_by_id = {}

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

        is_shelter = (org_type == "shelter") or bool(SHELTER_NAME_HINTS.search(shelter_name))
        is_rescue = bool(RESCUE_EXCLUDE_HINTS.search(shelter_name)) and not bool(SHELTER_NAME_HINTS.search(shelter_name))
        if not is_shelter or is_rescue:
            continue

        desc = (attrs.get("descriptionText") or attrs.get("descriptionHtml") or "").strip()
        direct_url = ""
        for cand in (attrs.get("url"), attrs.get("adoptionUrl")):
            direct_url = is_direct_dog_url(cand)
            if direct_url:
                break
        if not direct_url:
            for match in URL_IN_TEXT.findall(desc):
                direct_url = is_direct_dog_url(match)
                if direct_url:
                    break
        if not direct_url:
            continue

        name = (attrs.get("name") or "").strip()
        kill_date = (attrs.get("killDate") or "").strip()
        kill_reason = (attrs.get("killReason") or "").strip()
        dog_text = f"{name} {desc} {kill_reason}"
        is_urgent = bool(kill_date or kill_reason or URGENT_KEYWORDS.search(dog_text))

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

        updated_iso = updated_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if updated_dt else now_iso
        results.append({
            "id": f"rg-shelter-{animal.get('id')}",
            "animal_id": (attrs.get("rescueId") or animal.get("id") or "").strip(),
            "name": name or "Unnamed Dog",
            "breed": (attrs.get("breedString") or "").strip(),
            "shelter": shelter_name,
            "city": city,
            "county": county,
            "state": state,
            "size": (attrs.get("sizeGroup") or "").strip(),
            "age": (attrs.get("ageGroup") or "").strip(),
            "urgency_type": "Urgent / Deadline" if is_urgent else "Municipal Shelter",
            "deadline_or_urgency": f"Deadline {kill_date[:10]}" if kill_date else ("Urgent / Deadline" if is_urgent else "Municipal Shelter"),
            "needs_type": "Rescue Pull / Foster" if "rescue only" in dog_text.lower() else "Foster or Adopt",
            "transport_note": "",
            "photo_url": photo_url,
            "listing_url": direct_url,
            "updated_at": updated_iso,
            "last_checked": now_iso,
        })

    print(f"RescueGroups municipal shelter dogs with direct URLs: {len(results)}")
    return results


def main():
    dogs = []
    dogs.extend(scrape_oakland_animal_services())
    dogs.extend(scrape_sf_acc())
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
            0 if "Urgent" in d.get("deadline_or_urgency", "") or "Deadline" in d.get("deadline_or_urgency", "") else 1,
            -(parse_iso_date(d.get("updated_at") or "").timestamp() if parse_iso_date(d.get("updated_at") or "") else 0),
        )
    )

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(unique_dogs, f, indent=2)
        f.write("\n")

    print(f"Total municipal shelter dogs saved to dogs.json: {len(unique_dogs)}")


if __name__ == "__main__":
    main()
