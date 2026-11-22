import json
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

URGENT_KEYWORDS = re.compile(
    r"\b(at risk|urgent|euthanasia|euth|deadline|must exit|code red|rescue only|last call|plea|critical|immediate exit|medical|parvo|injured|overcrowded|space)\b",
    re.IGNORECASE,
)

# High-intake California municipal & county shelters on 24Petconnect
PETCONNECT_BOARDS = [
    {
        "url": "https://24petconnect.com/BreedRequest/Adopt?shelterlist=RVSD,RVSD1,RVSD2,RVSD3",
        "default_shelter": "Riverside County Animal Services",
        "city": "Jurupa Valley",
        "county": "Riverside",
    },
    {
        "url": "https://24petconnect.com/BreedRequest/Adopt?shelterlist=LACO,LACO1,LACO2,LACO3,LACO4,LACO5,LACO6",
        "default_shelter": "LA County Animal Care & Control",
        "city": "Downey",
        "county": "Los Angeles",
    },
    {
        "url": "https://24petconnect.com/BreedRequest/Adopt?shelterlist=SBCO,SBCO1,SBCT",
        "default_shelter": "San Bernardino County / City Shelter",
        "city": "San Bernardino",
        "county": "San Bernardino",
    },
    {
        "url": "https://24petconnect.com/BreedRequest/Adopt?shelterlist=KERN,STSL,STKN",
        "default_shelter": "Central Valley County Shelter",
        "city": "Bakersfield / Modesto",
        "county": "Kern / Stanislaus",
    },
]

# Bay Area municipal shelters on Adopt-a-Pet
ADOPTAPET_MUNICIPAL_SHELTERS = [
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
]


def detect_urgency_type(text: str, is_long_stay: bool = False) -> str:
    lower = text.lower()
    if "deadline" in lower or "must exit" in lower or "euth" in lower:
        return "Euthanasia / Exit Deadline"
    if "medical" in lower or "parvo" in lower or "injured" in lower or "sick" in lower:
        return "Medical Urgent"
    if "rescue only" in lower or "behavior" in lower:
        return "Rescue Only / Behavior"
    if "at risk" in lower:
        return "At-Risk Shelter Dog"
    if is_long_stay:
        return "Long-Stay At Risk (30d+)"
    return "High-Intake Municipal Shelter"


def days_since_intake(text: str) -> int:
    match = re.search(r"since\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", text)
    if not match:
        return 0
    try:
        dt = datetime.strptime(match.group(1), "%b %d, %Y").replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except ValueError:
        return 0


def scrape_24petconnect(page, now_iso: str) -> list[dict]:
    results = []
    seen_urls = set()

    for board in PETCONNECT_BOARDS:
        print(f"Loading 24Petconnect board: {board['default_shelter']}...")
        try:
            page.goto(board["url"], wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"Board load error ({board['default_shelter']}): {exc}")
            continue

        # Collect direct animal profile links rendered by the JS app
        links = page.eval_on_selector_all(
            "a[href*='/DetailsMain/'], a[href*='/Details/']",
            "els => els.map(e => ({href: e.href, text: (e.innerText || '').trim()}))",
        )

        candidate_urls = []
        for item in links:
            href = (item.get("href") or "").split("?")[0]
            if href and href not in seen_urls and ("/DetailsMain/" in href or "/Details/" in href):
                seen_urls.add(href)
                candidate_urls.append(href)

        print(f"  Found {len(candidate_urls)} animal profile links on {board['default_shelter']}")

        # Inspect up to 45 profiles per board so the job finishes in ~60 seconds
        for detail_url in candidate_urls[:45]:
            try:
                page.goto(detail_url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1500)
                body_text = page.inner_text("body")
            except Exception:
                continue

            # Skip cats/other species or already rescued animals
            lower_text = body_text.lower()
            if "i have been rescued" in lower_text:
                continue
            if not any(w in lower_text for w in ("dog", "terrier", "shepherd", "retriever", "husky", "pit bull", "chihuahua", "mix", "hound", "boxer", "rottweiler", "doberman")):
                continue

            stay_days = days_since_intake(body_text)
            is_urgent = bool(URGENT_KEYWORDS.search(body_text))
            if not (is_urgent or stay_days >= 30):
                continue

            id_match = re.search(r"/([A-Z]\d{5,8})$", detail_url, re.IGNORECASE)
            animal_id = id_match.group(1).upper() if id_match else detail_url.rstrip("/").split("/")[-1]

            name_match = re.search(r"Name:\s*([^\n\r]+)", body_text)
            staff_name_match = re.search(r"named me\s+([A-Za-z0-9 '-]+)\.", body_text)
            name = (
                name_match.group(1).strip()
                if name_match
                else (staff_name_match.group(1).strip() if staff_name_match else f"Dog #{animal_id}")
            )

            loc_match = re.search(r"Located At:\s*([^\n\r]+)", body_text)
            shelter_name = loc_match.group(1).strip() if loc_match else board["default_shelter"]

            photo_url = page.eval_on_selector(
                "img[src*='24petconnect'], img[src*='petplace'], img[src*='amazonaws']",
                "el => el ? el.src : ''",
            ) or ""

            needs = "Rescue Pull / Foster" if ("rescue only" in lower_text or "transfer" in lower_text) else "Foster or Adopt"
            deadline_match = re.search(r"(DEADLINE[^\n\r]*|MUST EXIT[^\n\r]*)", body_text, re.IGNORECASE)
            badge = deadline_match.group(1).strip()[:36] if deadline_match else ("At-Risk / Urgent" if is_urgent else f"Long-Stay ({stay_days}d)")

            results.append({
                "id": f"24pc-{animal_id.lower()}",
                "animal_id": animal_id,
                "name": name,
                "breed": "",
                "shelter": shelter_name,
                "city": board["city"],
                "county": board["county"],
                "state": "CA",
                "size": "",
                "age": "",
                "urgency_type": detect_urgency_type(body_text, stay_days >= 30),
                "deadline_or_urgency": badge,
                "needs_type": needs,
                "transport_note": "",
                "photo_url": photo_url,
                "listing_url": detail_url,
                "updated_at": now_iso,
                "last_checked": now_iso,
            })

    print(f"[1/2] 24Petconnect verified urgent/at-risk county dogs: {len(results)}")
    return results


def scrape_adoptapet_municipal(page, now_iso: str) -> list[dict]:
    results = []
    seen_urls = set()

    for sh in ADOPTAPET_MUNICIPAL_SHELTERS:
        print(f"Loading Adopt-a-Pet municipal page: {sh['shelter']}...")
        try:
            page.goto(sh["url"], wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"Adopt-a-Pet load error ({sh['shelter']}): {exc}")
            continue

        cards = page.eval_on_selector_all(
            "a[href*='/pet/']",
            """els => els.map(e => {
                const img = e.querySelector('img');
                return {
                    href: e.href,
                    text: (e.innerText || '').trim(),
                    img: img ? img.src : ''
                };
            })""",
        )

        for c in cards:
            href = (c.get("href") or "").split("?")[0]
            text = c.get("text") or ""
            if not href or href in seen_urls:
                continue
            # Filter out non-dog animals
            if any(non_dog in text.lower() for non_dog in ("cat", "kitten", "shorthair", "longhair", "guinea pig", "rabbit", "bunny")):
                continue
            seen_urls.add(href)

            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            name = lines[0] if lines else "Shelter Dog"
            breed = lines[1] if len(lines) > 1 else ""
            pet_id = href.rstrip("/").split("/")[-1]

            results.append({
                "id": f"aap-{pet_id}",
                "animal_id": pet_id,
                "name": name,
                "breed": breed,
                "shelter": sh["shelter"],
                "city": sh["city"],
                "county": sh["county"],
                "state": "CA",
                "size": "",
                "age": "",
                "urgency_type": "Bay Area Municipal Shelter",
                "deadline_or_urgency": "Municipal Shelter",
                "needs_type": "Foster or Adopt",
                "transport_note": "",
                "photo_url": c.get("img") or "",
                "listing_url": href,
                "updated_at": now_iso,
                "last_checked": now_iso,
            })

    print(f"[2/2] Bay Area Adopt-a-Pet municipal dogs: {len(results)}")
    return results


def main():
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dogs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        dogs.extend(scrape_24petconnect(page, now_iso))
        dogs.extend(scrape_adoptapet_municipal(page, now_iso))

        browser.close()

    dogs.sort(
        key=lambda d: (
            0 if d.get("county") != "San Francisco" and d.get("deadline_or_urgency") != "Municipal Shelter" else 1,
            d.get("county", ""),
            d.get("name", ""),
        )
    )

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(dogs, f, indent=2)
        f.write("\n")

    print(f"Total direct municipal shelter dogs written to dogs.json: {len(dogs)}")


if __name__ == "__main__":
    main()
