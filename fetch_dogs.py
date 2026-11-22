import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

NON_DOG_PATTERN = re.compile(
    r"\b(animal type:\s*(cat|bird|rabbit|other)|domestic shorthair|domestic mediumhair|domestic longhair|siamese|calico|tabby|kitten|chicken|pigeon|rabbit|bunny|guinea pig|hamster|ferret)\b",
    re.IGNORECASE,
)

RESCUED_PATTERN = re.compile(
    r"\b(i have been rescued|commitment rcvd|adopted|rescue hold|pending pickup)\b",
    re.IGNORECASE,
)


def clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def extract_field(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        m = re.search(rf"{re.escape(label)}\s*:\s*([^\n\r|]+)", text, re.IGNORECASE)
        if m:
            return clean_space(m.group(1))
    return ""


def parse_date_days_ago(val) -> int:
    if val is None or val == "":
        return 0
    if isinstance(val, (int, float)) and val > 1_000_000_000_000:
        dt = datetime.fromtimestamp(val / 1000.0, tz=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    s = str(val).strip()
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            dt = datetime.strptime(s[:20], fmt).replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - dt).days)
        except ValueError:
            continue
    return 0


def find_petconnect_cards_across_frames(page) -> list[dict]:
    """Extracts 24Petconnect animal cards from the main page or embedded county iframes."""
    all_cards = []
    seen_keys = set()

    for frame in page.frames:
        try:
            cards = frame.evaluate(
                """() => {
                    const out = [];
                    const links = Array.from(document.querySelectorAll("a[href*='Details'], a[href*='details']"));
                    const containers = new Set();

                    for (const a of links) {
                        const card = a.closest(".gridResult, .pet-card, .animal-card, .card, li, article, tr, div[class*='result'], div[class*='Result']") || a.parentElement || a;
                        if (containers.has(card)) continue;
                        containers.add(card);
                        const img = card.querySelector("img");
                        const txt = (card.innerText || a.innerText || "").trim();
                        if (txt.length < 6) continue;
                        out.push({
                            href: a.href || "",
                            text: txt,
                            img: img ? (img.src || img.getAttribute("data-src") || "") : ""
                        });
                    }

                    // Fallback if cards don't wrap standard <a> links
                    if (out.length === 0) {
                        const gridItems = Array.from(document.querySelectorAll(".gridResult, [class*='gridResult'], [class*='petCard']"));
                        for (const el of gridItems) {
                            const txt = (el.innerText || "").trim();
                            if (txt.length < 10) continue;
                            const a = el.querySelector("a[href]");
                            const img = el.querySelector("img");
                            out.push({
                                href: a ? a.href : "",
                                text: txt,
                                img: img ? (img.src || img.getAttribute("data-src") || "") : ""
                            });
                        }
                    }
                    return out;
                }"""
            )
        except Exception:
            continue

        for c in cards:
            key = (c.get("href") or "") + "|" + (c.get("text") or "")[:80]
            if key not in seen_keys:
                seen_keys.add(key)
                all_cards.append(c)

    return all_cards


# ---------------------------------------------------------------------------
# 1. STANISLAUS COUNTY — RED STATUS & FINAL CALL (Direct HTML)
# ---------------------------------------------------------------------------
def scrape_stanislaus_red(page, now_iso: str) -> list[dict]:
    url = "https://www.stanislausanimalservices.com/atrisk/adopt-dogs.shtm?color=red&sort=orderByIntakeDate"
    print("[1/7] Scraping Stanislaus County RED / Final Call dogs...")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(3000)
    except Exception as exc:
        print(f"  Stanislaus error: {exc}")
        return []

    raw_cards = page.evaluate(
        """() => {
            const results = [];
            const idRegex = /\\bA\\d{5,7}\\b/;
            const blocks = Array.from(document.querySelectorAll("div, tr, li, article, section"));
            const seenIds = new Set();

            for (const el of blocks) {
                const txt = (el.innerText || "").trim();
                if (!txt || txt.length < 25 || txt.length > 1800) continue;
                const m = txt.match(idRegex);
                if (!m) continue;
                // Pick the smallest container that holds an animal ID and an image or details
                const childMatch = Array.from(el.children).some(ch => idRegex.test(ch.innerText || "") && ch.querySelector("img"));
                if (childMatch) continue;

                const aid = m[0];
                if (seenIds.has(aid)) continue;
                seenIds.add(aid);

                const img = el.querySelector("img");
                const link = el.querySelector("a[href]");
                results.push({
                    animal_id: aid,
                    text: txt,
                    img: img ? img.src : "",
                    href: link ? link.href : window.location.href
                });
            }
            return results;
        }"""
    )

    results = []
    for item in raw_cards:
        txt = item.get("text", "")
        # Skip dogs already safely in foster homes (F01)
        if re.search(r"\bF01\b|in a foster home", txt, re.IGNORECASE):
            continue

        aid = item["animal_id"]
        lines = [clean_space(ln) for ln in txt.splitlines() if clean_space(ln)]

        # Name usually appears right before the Animal ID line or at the top of the card
        name = f"Stanislaus {aid}"
        for idx, ln in enumerate(lines):
            if aid in ln and idx > 0 and len(lines[idx - 1]) < 35 and "finalcall" not in lines[idx - 1].lower():
                name = lines[idx - 1]
                break
        if name.startswith("Stanislaus ") and lines:
            for ln in lines[:3]:
                if len(ln) < 30 and not re.search(r"\b(A\d{5,7}|finalcall|male|female|\d+\s*days)\b", ln, re.IGNORECASE):
                    name = ln
                    break

        is_final_call = bool(re.search(r"final\s*call", txt, re.IGNORECASE))
        days_match = re.search(r"(\d+)\s*days", txt, re.IGNORECASE)
        days_str = f"{days_match.group(1)} days in shelter" if days_match else ""
        age_match = re.search(r"(\d+\s*Years?(?:\s*\d+\s*Months?)?|\d+\s*Months?)", txt, re.IGNORECASE)
        age = age_match.group(1) if age_match else ""

        href = item.get("href") or url
        if href.startswith("mailto:") or href.startswith("javascript:"):
            href = url

        results.append({
            "id": f"stanislaus-{aid.lower()}",
            "animal_id": aid,
            "name": name,
            "breed": "",
            "shelter": "Stanislaus Animal Services (Modesto)",
            "city": "Modesto",
            "county": "Stanislaus",
            "state": "CA",
            "size": "",
            "age": age,
            "urgency_type": "Final Call (Tue/Wed Deadline)" if is_final_call else "RED Status (At Risk)",
            "deadline_or_urgency": "FINAL CALL" if is_final_call else "RED Status Urgent",
            "needs_type": "Rescue Pull, Foster, or Adopt",
            "transport_note": clean_space(f"{days_str}. Must exit by Tue COB / Wed 6:30am if Final Call.") if is_final_call else days_str,
            "photo_url": item.get("img") or "",
            "listing_url": href,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"  Stanislaus RED/Final Call kennel dogs saved: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# 2. OAKLAND ANIMAL SERVICES — /rescue-needed/ (Shelterluv Embed)
# ---------------------------------------------------------------------------
def scrape_oas_rescue_needed(page, now_iso: str) -> list[dict]:
    url = "https://www.oaklandanimalservices.org/rescue-needed/"
    print("[2/7] Scraping Oakland Animal Services /rescue-needed/...")
    try:
        page.goto(url, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(4000)
    except Exception as exc:
        print(f"  OAS error: {exc}")
        return []

    animal_ids = []
    seen_ids = set()
    for frame in page.frames:
        try:
            html_blob = frame.content()
        except Exception:
            continue
        for m in re.finditer(r"animalId=(\d+)", html_blob):
            if m.group(1) not in seen_ids:
                seen_ids.add(m.group(1))
                animal_ids.append(m.group(1))

    print(f"  Found {len(animal_ids)} OAS /rescue-needed/ IDs")
    results = []
    for aid in animal_ids:
        detail_url = f"https://www.oaklandanimalservices.org/rescue-needed/animal/?animalId={aid}"
        try:
            page.goto(detail_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
        except Exception:
            continue

        combined_text = ""
        photo_url = ""
        for frame in page.frames:
            try:
                txt = frame.inner_text("body")
                if txt:
                    combined_text += "\n" + txt
                if not photo_url:
                    imgs = frame.eval_on_selector_all("img", "els => els.map(i => i.src || '')")
                    for src in imgs:
                        if src and not src.endswith(".svg") and "logo" not in src.lower() and ("shelterluv" in src.lower() or "amazonaws" in src.lower()):
                            photo_url = src
                            break
            except Exception:
                continue

        if NON_DOG_PATTERN.search(combined_text[:500]):
            continue

        lines = [clean_space(ln) for ln in combined_text.splitlines() if clean_space(ln)]
        name = f"OAS #{aid}"
        shelter_id = aid
        breed = ""
        for idx, ln in enumerate(lines):
            if ln.lower().startswith("animal id"):
                m = re.search(r"([A-Z]?\d{4,8})", ln)
                if m:
                    shelter_id = m.group(1)
                if idx > 0 and len(lines[idx - 1]) < 40:
                    name = lines[idx - 1]
                break

        for ln in lines:
            if len(ln) < 55 and any(k in ln.lower() for k in ("mix", "pit", "terrier", "shepherd", "husky", "retriever", "hound", "chihuahua", "malinois", "doberman")):
                breed = ln
                break

        deadline_match = re.search(r"(DEADLINE[^\n\r]*|OUT OF TIME[^\n\r]*|PRIORITY[^\n\r]*)", combined_text, re.IGNORECASE)
        badge = clean_space(deadline_match.group(1))[:36] if deadline_match else "OAS Rescue Needed"

        results.append({
            "id": f"oas-{shelter_id.lower()}",
            "animal_id": shelter_id,
            "name": name,
            "breed": breed,
            "shelter": "Oakland Animal Services",
            "city": "Oakland",
            "county": "Alameda",
            "state": "CA",
            "size": "",
            "age": "",
            "urgency_type": "OAS Rescue Needed / At Risk",
            "deadline_or_urgency": badge,
            "needs_type": "Rescue Pull, Foster, or Adopt",
            "transport_note": "Listed directly on OAS Rescue Needed urgent board",
            "photo_url": photo_url,
            "listing_url": detail_url,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"  OAS /rescue-needed/ dogs saved: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# 3-6. 24PETCONNECT MUNICIPAL URGENT BOARDS (Kern, Contra Costa, Devore, Riverside)
# ---------------------------------------------------------------------------
def scrape_24petconnect_board(
    page,
    now_iso: str,
    step_label: str,
    url: str,
    shelter_name: str,
    city: str,
    county: str,
    portal_code: str,
    require_urgent_flag: bool = False,
    inspect_details_for_deadline: bool = False,
    default_badge: str = "Urgent / Rescue Needed",
    default_urgency: str = "At-Risk Shelter Dog",
) -> list[dict]:
    print(f"{step_label} Scraping {shelter_name} ({url})...")
    try:
        page.goto(url, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(4500)
    except Exception as exc:
        print(f"  Load error ({shelter_name}): {exc}")
        return []

    raw_cards = find_petconnect_cards_across_frames(page)
    print(f"  Detected {len(raw_cards)} raw cards on {shelter_name}")

    results = []
    seen_ids = set()

    for c in raw_cards:
        txt = c.get("text") or ""
        href = (c.get("href") or "").split("?")[0]

        if NON_DOG_PATTERN.search(txt) or RESCUED_PATTERN.search(txt):
            continue

        # Extract Animal ID (e.g., A1261085, A855351, A1053905, A1937091)
        id_match = re.search(r"\b(A\d{5,8})\b", txt) or re.search(r"/(A\d{5,8})\b", href)
        animal_id = id_match.group(1).upper() if id_match else ""
        if not animal_id or animal_id in seen_ids:
            continue

        # Filter strictly for Devore / mixed adoption boards
        if require_urgent_flag:
            has_flag = bool(
                re.search(
                    r"(UrgentFlag:\s*URGENT|Meet:\s*URGENT|Status:\s*Rescue Onl|Substatus:\s*U-(Behavior|LOS|Medical)|at risk|deadline)",
                    txt,
                    re.IGNORECASE,
                )
            )
            if not has_flag:
                continue

        seen_ids.add(animal_id)

        name = extract_field(txt, ("Meet", "Name"))
        if name:
            name = re.sub(r"\s*\(A\d{5,8}\)\s*", "", name).replace("URGENT ", "").strip()
        if not name:
            name = f"Dog {animal_id}"

        breed = extract_field(txt, ("Breed",))
        age = extract_field(txt, ("Age",))
        reason = extract_field(txt, ("Reason for Rescue", "Substatus"))
        status = extract_field(txt, ("Status",))
        location = extract_field(txt, ("Located at", "Location")) or shelter_name
        photo_url = c.get("img") or ""

        if not href:
            href = f"https://24petconnect.com/{portal_code}/Details/{portal_code}/{animal_id}"

        urgency_type = default_urgency
        badge = default_badge
        note = reason
        needs = "Rescue Pull Only" if re.search(r"rescue onl", status + " " + txt, re.IGNORECASE) else "Rescue Pull / Foster / Adopt"

        if reason:
            if "u-behavior" in reason.lower():
                urgency_type = "Urgent Behavior (Euthanasia Risk)"
                badge = "URGENT: U-Behavior"
            elif "u-medical" in reason.lower() or "uri" in reason.lower() or "med" in reason.lower():
                urgency_type = "Urgent Medical"
                badge = f"URGENT: {reason[:24]}"
            elif "u-los" in reason.lower():
                urgency_type = "Urgent Length-of-Stay (30d+)"
                badge = "URGENT: U-LOS"
            else:
                badge = f"At Risk: {reason[:24]}"

        results.append({
            "id": f"24pc-{portal_code.lower()}-{animal_id.lower()}",
            "animal_id": animal_id,
            "name": name,
            "breed": breed,
            "shelter": location,
            "city": city,
            "county": county,
            "state": "CA",
            "size": extract_field(txt, ("Weight", "Size")),
            "age": age,
            "urgency_type": urgency_type,
            "deadline_or_urgency": badge,
            "needs_type": needs,
            "transport_note": note,
            "photo_url": photo_url,
            "listing_url": href,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    # Optional detail-page verification (used for Riverside RVSDPublic to capture exact ***DEADLINE*** & filter out RESCUED)
    if inspect_details_for_deadline and results:
        verified = []
        for dog in results[:35]:
            try:
                page.goto(dog["listing_url"], wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1200)
                body = page.inner_text("body")
            except Exception:
                continue

            if RESCUED_PATTERN.search(body) or NON_DOG_PATTERN.search(body[:600]):
                continue

            is_at_risk = bool(re.search(r"at risk animal|deadline|must exit|rescue only|extremely urgent|parvo|medical", body, re.IGNORECASE))
            if not is_at_risk:
                continue

            dl = re.search(r"(\*{0,3}DEADLINE[^\n\r*]{0,50})", body, re.IGNORECASE)
            if dl:
                dog["deadline_or_urgency"] = clean_space(dl.group(1).replace("*", ""))[:38]
                dog["urgency_type"] = "Euthanasia / Exit Deadline"

            note_match = re.search(r"More Info:\s*([^\n\r]{10,220})", body, re.IGNORECASE)
            if note_match:
                dog["transport_note"] = clean_space(note_match.group(1))[:160]

            if not dog["photo_url"]:
                img_src = page.eval_on_selector("img[src*='24petconnect'], img[src*='petplace'], img[src*='amazonaws']", "el => el ? el.src : ''")
                if img_src:
                    dog["photo_url"] = img_src

            verified.append(dog)
        results = verified

    print(f"  {shelter_name} urgent dogs saved: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# 7. SAN JOSE ACS — ARCGIS REST JSON API (Inspects Schema + Flags At-Risk Dogs)
# ---------------------------------------------------------------------------
def fetch_sanjose_arcgis(now_iso: str) -> list[dict]:
    print("[7/7] Querying San Jose ACS ArcGIS MapServer Layer 1 (JSON)...")
    api_url = (
        "https://geo.sanjoseca.gov/server/rest/services/DPW/DPW_AnimalCareServices/"
        "MapServer/1/query?where=ANIMALTYPE%3D%27Dog%27&outFields=*&returnGeometry=false&f=json"
    )
    pet_compass_url = (
        "https://experience.arcgis.com/experience/6703c042c7534f5fb2999c57227fda13/"
        "?data_filter=dataSource_1-DPW_AnimalCareServices_5571:ANIMALTYPE=%27Dog%27"
    )
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        print(f"  San Jose ArcGIS API error: {exc}")
        return []

    features = data.get("features", [])
    print(f"  San Jose ArcGIS returned {len(features)} sheltered dogs")
    if features:
        sample_attrs = features[0].get("attributes", {})
        print(f"  San Jose ArcGIS schema fields: {list(sample_attrs.keys())}")

    urgent_regex = re.compile(r"\b(urgent|rescue|special|medical|at risk|euth|behavior|transfer|hold)\b", re.IGNORECASE)
    results = []

    for feat in features:
        attrs = feat.get("attributes", {})
        joined_vals = " ".join(str(v) for v in attrs.values() if v is not None)

        # Look dynamically for intake date or urgent/rescue/medical flags in any field
        stay_days = 0
        for k, v in attrs.items():
            if any(tok in k.lower() for tok in ("intake", "arrival", "in_date", "indate", "datein")):
                stay_days = max(stay_days, parse_date_days_ago(v))

        has_urgent_flag = bool(urgent_regex.search(joined_vals))
        # Only include dogs explicitly flagged in status/notes OR staying 45+ days in kennel
        if not (has_urgent_flag or stay_days >= 45):
            continue

        aid = str(
            attrs.get("ANIMALID")
            or attrs.get("PETID")
            or attrs.get("ENTERPRISEID")
            or attrs.get("FACILITYID")
            or attrs.get("OBJECTID")
            or ""
        ).strip()
        name = str(attrs.get("PETNAME") or attrs.get("ANIMALNAME") or attrs.get("NAME") or f"San Jose {aid}").strip()
        breed = str(attrs.get("PRIMARYBREED") or attrs.get("BREED") or attrs.get("PETBREED") or "").strip()
        age = str(attrs.get("PETAGE") or "").strip()
        size = str(attrs.get("PETSIZE") or "").strip()
        photo_url = str(attrs.get("PHOTOURL") or attrs.get("IMAGEURL") or attrs.get("PHOTOLINK") or "").strip()
        direct_url = str(attrs.get("PETURL") or attrs.get("URL") or attrs.get("WEBLINK") or pet_compass_url).strip()

        results.append({
            "id": f"sj-acs-{aid.lower()}",
            "animal_id": aid,
            "name": name,
            "breed": breed,
            "shelter": "San Jose Animal Care Center",
            "city": "San Jose",
            "county": "Santa Clara",
            "state": "CA",
            "size": size,
            "age": age,
            "urgency_type": "Long-Stay Kennel Dog (45d+)" if stay_days >= 45 else "Special Needs / Urgent Flag",
            "deadline_or_urgency": f"Long-Stay ({stay_days}d)" if stay_days >= 45 else "SJ ACS Flagged",
            "needs_type": "Foster or Adopt",
            "transport_note": f"Kennel {attrs.get('KENNELNAME', '')}".strip(),
            "photo_url": photo_url,
            "listing_url": direct_url,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"  San Jose ArcGIS flagged/long-stay dogs saved: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# DIAGNOSTIC CHECK: LA Animal Services New Hope Alert Links
# ---------------------------------------------------------------------------
def inspect_laas_newhope_links(page) -> None:
    try:
        page.goto("https://www.laanimalservices.com/our-partners", wait_until="domcontentloaded", timeout=25000)
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.filter(e => /dog/i.test(e.innerText || '') && /alert|newhope|24pet|shelter|pdf/i.test(e.href || '')).map(e => ({text: e.innerText.trim(), href: e.href}))",
        )
        print(f"[Info] LAAS New Hope dog alert links currently live: {links[:8]}")
    except Exception as exc:
        print(f"[Info] LAAS check skipped: {exc}")


def main():
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_dogs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 1800})
        page = context.new_page()

        # Tier 1: 100% Urgent / At-Risk Municipal Boards
        all_dogs.extend(scrape_stanislaus_red(page, now_iso))
        all_dogs.extend(scrape_oas_rescue_needed(page, now_iso))
        all_dogs.extend(
            scrape_24petconnect_board(
                page=page,
                now_iso=now_iso,
                step_label="[3/7]",
                url="https://24petconnect.com/KernCountyRescue?at=DOG",
                shelter_name="Kern County Animal Services (Greatest Risk)",
                city="Bakersfield",
                county="Kern",
                portal_code="KernCountyRescue",
                require_urgent_flag=False,
                default_badge="Animals at Greatest Risk",
                default_urgency="Immediate Rescue Commitment Needed",
            )
        )
        all_dogs.extend(
            scrape_24petconnect_board(
                page=page,
                now_iso=now_iso,
                step_label="[4/7]",
                url="https://www.contracosta.ca.gov/7887/Transfer-Preferred-Dogs",
                shelter_name="Contra Costa Animal Services - Martinez",
                city="Martinez",
                county="Contra Costa",
                portal_code="CCAS",
                require_urgent_flag=False,
                default_badge="Transfer Preferred",
                default_urgency="Transfer Preferred / Rescue Priority",
            )
        )

        # Tier 2: Structured Urgent-Flag County Boards
        all_dogs.extend(
            scrape_24petconnect_board(
                page=page,
                now_iso=now_iso,
                step_label="[5/7]",
                url="https://animalcare.sbcounty.gov/pets/",
                shelter_name="San Bernardino County - Devore Shelter",
                city="San Bernardino",
                county="San Bernardino",
                portal_code="SBCO1",
                require_urgent_flag=True,
                default_badge="URGENT (Devore)",
                default_urgency="Urgent / Rescue Only",
            )
        )
        all_dogs.extend(
            scrape_24petconnect_board(
                page=page,
                now_iso=now_iso,
                step_label="[6/7]",
                url="https://24petconnect.com/RVSDRescue?at=DOG",
                shelter_name="Riverside County Animal Services (Rescue Only)",
                city="Jurupa Valley",
                county="Riverside",
                portal_code="RVSDRescue",
                require_urgent_flag=False,
                inspect_details_for_deadline=True,
                default_badge="48-Hr Rescue Only",
                default_urgency="Rescue Only / At Risk",
            )
        )
        all_dogs.extend(
            scrape_24petconnect_board(
                page=page,
                now_iso=now_iso,
                step_label="[6b/7]",
                url="https://24petconnect.com/RVSDPublic?at=DOG",
                shelter_name="Riverside County Animal Services (Red List)",
                city="Jurupa Valley",
                county="Riverside",
                portal_code="RVSDPublic",
                require_urgent_flag=False,
                inspect_details_for_deadline=True,
                default_badge="72-Hr Red List",
                default_urgency="Red List / Exit Deadline",
            )
        )

        inspect_laas_newhope_links(page)
        browser.close()

    # Tier 2 API: San Jose ArcGIS MapServer
    all_dogs.extend(fetch_sanjose_arcgis(now_iso))

    # Deduplicate by shelter + animal_id
    seen = set()
    unique_dogs = []
    for dog in all_dogs:
        key = f"{dog.get('county')}-{dog.get('animal_id')}".lower()
        if key not in seen:
            seen.add(key)
            unique_dogs.append(dog)

    # Sort highest-urgency deadlines first
    def sort_rank(d: dict) -> tuple:
        badge = (d.get("deadline_or_urgency") or "").upper()
        if "FINAL CALL" in badge or "DEADLINE" in badge:
            tier = 0
        elif "URGENT" in badge or "RESCUE" in badge or "RED" in badge:
            tier = 1
        elif "GREATEST RISK" in badge or "TRANSFER" in badge:
            tier = 2
        else:
            tier = 3
        return (tier, d.get("county", ""), d.get("name", ""))

    unique_dogs.sort(key=sort_rank)

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(unique_dogs, f, indent=2)
        f.write("\n")

    print(f"TOTAL VERIFIED MUNICIPAL URGENT DOGS SAVED: {len(unique_dogs)}")


if __name__ == "__main__":
    main()
