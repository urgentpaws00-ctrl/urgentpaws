import json
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

URGENT_KEYWORDS = re.compile(
    r"\b(at risk|urgent|euthanasia|euth|deadline|must exit|code red|rescue only|last call|plea|critical|immediate exit|medical|special needs|parvo|injured|heartworm|senior|hospice|overcrowded|space|long[- ]stay)\b",
    re.IGNORECASE,
)

MUNICIPAL_SHELTERS = [
    # Bay Area
    {
        "url": "https://www.adoptapet.com/shelter/86673-oakland-animal-services-oakland-california",
        "shelter": "Oakland Animal Services",
        "city": "Oakland",
        "county": "Alameda",
    },
    {
        "url": "https://www.adoptapet.com/shelter/73415-san-francisco-animal-care-control-san-francisco-california",
        "shelter": "SF Animal Care & Control",
        "city": "San Francisco",
        "county": "San Francisco",
    },
    {
        "url": "https://www.adoptapet.com/shelter/85845-east-county-animal-shelter-dublin-california",
        "shelter": "Alameda County East County Shelter",
        "city": "Dublin",
        "county": "Alameda",
    },
    {
        "url": "https://www.adoptapet.com/shelter/74384-santa-clara-county-animal-shelter-san-martin-california",
        "shelter": "Santa Clara County Animal Shelter",
        "city": "San Martin",
        "county": "Santa Clara",
    },
    {
        "url": "https://www.adoptapet.com/shelter/69854-sonoma-county-animal-care-and-control-santa-rosa-california",
        "shelter": "Sonoma County Animal Services",
        "city": "Santa Rosa",
        "county": "Sonoma",
    },
    # Central Valley (High-Intake)
    {
        "url": "https://www.adoptapet.com/shelter/79697-city-of-stockton-animal-shelter-stockton-california",
        "shelter": "City of Stockton Animal Shelter",
        "city": "Stockton",
        "county": "San Joaquin",
    },
    {
        "url": "https://www.adoptapet.com/shelter/73661-central-california-spca-fresno-california",
        "shelter": "Central California SPCA / Fresno",
        "city": "Fresno",
        "county": "Fresno",
    },
    # Southern California (High-Intake)
    {
        "url": "https://www.adoptapet.com/shelter/81549-city-of-san-bernardino-department-of-animal-services-san-bernardino-california",
        "shelter": "City of San Bernardino Animal Services",
        "city": "San Bernardino",
        "county": "San Bernardino",
    },
    {
        "url": "https://www.adoptapet.com/shelter/263681-riverside-county-animal-control-san-jacinto-shelter-san-jacinto-california",
        "shelter": "Riverside County Animal Control (San Jacinto)",
        "city": "San Jacinto",
        "county": "Riverside",
    },
    {
        "url": "https://www.adoptapet.com/shelter/84396-santa-barbara-county-animal-services-santa-barbara-shelter-goleta-california",
        "shelter": "Santa Barbara County Animal Services",
        "city": "Goleta",
        "county": "Santa Barbara",
    },
]

NON_DOG_URL_SUFFIX = re.compile(
    r"-(bird|rabbit|cat|kitten|guinea-pig|hamster|ferret|reptile|pig|horse|barnyard)$",
    re.IGNORECASE,
)

NON_DOG_TEXT = re.compile(
    r"\b(chicken|pigeon|dove|parakeet|cockatiel|cat|kitten|domestic shorthair|domestic mediumhair|domestic longhair|siamese|calico|tabby|guinea pig|rabbit|bunny|hamster)\b",
    re.IGNORECASE,
)


def classify_urgency(text: str) -> tuple[str, str, bool]:
    lower = text.lower()
    if "deadline" in lower or "must exit" in lower or "euth" in lower or "at risk" in lower:
        return "Urgent / Exit Deadline", "Urgent / Deadline", True
    if "medical" in lower or "special needs" in lower or "injured" in lower or "parvo" in lower or "heartworm" in lower:
        return "Medical / Special Needs", "Urgent / Medical", True
    if "rescue only" in lower or "behavior" in lower:
        return "Rescue Only / Behavior", "Urgent / Rescue Only", True
    if "senior" in lower or "hospice" in lower:
        return "Senior / At-Risk", "Urgent / Senior", True
    return "High-Intake Municipal Shelter", "Municipal Shelter", False


def scrape_shelters(page, now_iso: str) -> list[dict]:
    results = []
    seen_urls = set()

    for sh in MUNICIPAL_SHELTERS:
        print(f"Checking {sh['shelter']}...")
        try:
            page.goto(sh["url"], wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(2500)
        except Exception as exc:
            print(f"  Skipped ({exc})")
            continue

        cards = page.eval_on_selector_all(
            "a[href*='/pet/']",
            """els => els.map(e => {
                const imgs = Array.from(e.querySelectorAll('img')).map(i => i.src || '');
                const realPhoto = imgs.find(src => src && !src.endsWith('.svg') && !src.includes('badge')) || '';
                return {
                    href: e.href,
                    text: (e.innerText || '').trim(),
                    img: realPhoto
                };
            })""",
        )

        shelter_count = 0
        for c in cards:
            href = (c.get("href") or "").split("?")[0]
            text = c.get("text") or ""
            if not href or href in seen_urls:
                continue
            if NON_DOG_URL_SUFFIX.search(href) or NON_DOG_TEXT.search(text):
                continue
            seen_urls.add(href)

            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            has_special_badge = lines and lines[0].lower() == "special needs"
            if has_special_badge:
                lines = lines[1:]

            name = lines[0] if lines else "Shelter Dog"
            breed = lines[1] if len(lines) > 1 else ""
            meta = lines[2] if len(lines) > 2 else ""

            age = ""
            for age_word in ("Puppy", "Young", "Adult", "Senior"):
                if age_word.lower() in meta.lower():
                    age = age_word
                    break

            pet_id = href.rstrip("/").split("/")[-1]
            combined_text = f"{'Special Needs ' if has_special_badge else ''}{name} {breed} {meta}"
            urgency_type, badge, is_urgent = classify_urgency(combined_text)
            needs = "Rescue Pull / Foster" if "rescue only" in combined_text.lower() else "Foster or Adopt"

            results.append({
                "id": f"aap-{pet_id}",
                "animal_id": pet_id.split("-")[0],
                "name": name,
                "breed": breed,
                "shelter": sh["shelter"],
                "city": sh["city"],
                "county": sh["county"],
                "state": "CA",
                "size": "",
                "age": age,
                "urgency_type": urgency_type,
                "deadline_or_urgency": badge,
                "needs_type": needs,
                "transport_note": "",
                "photo_url": c.get("img") or "",
                "listing_url": href,
                "updated_at": now_iso,
                "last_checked": now_iso,
                "_is_urgent": is_urgent,
            })
            shelter_count += 1

        print(f"  Found {shelter_count} dogs at {sh['shelter']}")

    high_intake_counties = {"Fresno", "San Joaquin", "San Bernardino", "Riverside", "Alameda", "Santa Clara"}
    candidates = [d for d in results if d["county"] in high_intake_counties][:35]
    print(f"Scanning {len(candidates)} individual dog bios for urgent/medical/rescue-only pleas...")

    for dog in candidates:
        try:
            page.goto(dog["listing_url"], wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1200)
            bio_text = page.inner_text("body")
        except Exception:
            continue

        id_match = re.search(r"\b([A-Z]\d{5,8})\b", bio_text)
        if id_match:
            dog["animal_id"] = id_match.group(1)

        if URGENT_KEYWORDS.search(bio_text):
            urgency_type, badge, _ = classify_urgency(bio_text)
            dog["urgency_type"] = urgency_type
            dog["deadline_or_urgency"] = badge
            dog["_is_urgent"] = True
            if "rescue only" in bio_text.lower():
                dog["needs_type"] = "Rescue Pull / Foster"

    results.sort(
        key=lambda d: (
            0 if d.get("_is_urgent") else 1,
            d.get("county", ""),
            d.get("name", ""),
        )
    )

    for d in results:
        d.pop("_is_urgent", None)

    return results


def main():
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        dogs = scrape_shelters(page, now_iso)
        browser.close()

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(dogs, f, indent=2)
        f.write("\n")

    print(f"Total California municipal shelter dogs saved to dogs.json: {len(dogs)}")


if __name__ == "__main__":
    main()
