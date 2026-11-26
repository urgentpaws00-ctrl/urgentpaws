from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
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

RESCUE_ONLY_PATTERN = re.compile(
    r"\b(rescue\s*only|rescue\s*pull\s*only|available\s+to\s+rescue\s+organizations?\s+only|501\s*\(c\)\s*\(3\)\s*only)\b",
    re.IGNORECASE,
)

DATE_PATTERN = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")

LAAS_WEB_BLOCKED = False
MAX_PRESERVED_DAYS = 7

LAAS_ALERT_BOARDS = [
    {
        "label": "LAAS Euthanasia Alert",
        "badge": "EUTHANASIA ALERT",
        "urgency_type": "LAAS Euthanasia Alert (6 Shelters)",
        "url": "https://petharbor.com/results.asp?WHERE=type_DOG&PAGE=1&searchtype=ALL&friends=1&samaritans=1&nosuccess=0&rows=50&imght=120&imgres=thumb&view=sysadm.v_lact_alert_euth&start=3&SHELTERLIST=%27LACT%27,%27LACT1%27,%27LACT4%27,%27LACT3%27,%27LACT2%27,%27LACT5%27,%27LACT6%27&OrderBy=shelter",
    },
    {
        "label": "LAAS Urgent Needs Alert",
        "badge": "URGENT NEEDS ALERT",
        "urgency_type": "LAAS Urgent Needs / Escalating Stress",
        "url": "https://petharbor.com/results.asp?WHERE=type_DOG&PAGE=1&searchtype=ALL&friends=1&samaritans=1&nosuccess=0&rows=50&imght=120&imgres=thumb&view=sysadm.v_lact_alert_yellow2&start=3&SHELTERLIST=%27LACT%27,%27LACT1%27,%27LACT4%27,%27LACT3%27,%27LACT2%27,%27LACT5%27,%27LACT6%27&OrderBy=shelter",
    },
    {
        "label": "LAAS Orange Alert (Urgent Medical)",
        "badge": "ORANGE MEDICAL ALERT",
        "urgency_type": "LAAS Urgent Medical (Orange Alert)",
        "url": "https://petharbor.com/results.asp?searchtype=ALL&start=3&friends=1&samaritans=1&nosuccess=0&orderby=shelter&rows=50&imght=120&imgres=Detail&view=sysadm.v_lact_alert_orange&shelterlist=%27LACT%27,%27LACT1%27,%27LACT4%27,%27LACT3%27,%27LACT2%27,%27LACT5%27,%27LACT6%27&where=type_DOG&PAGE=1",
    },
    {
        "label": "LAAS Blue Alert (Severe Medical)",
        "badge": "BLUE MEDICAL ALERT",
        "urgency_type": "LAAS Severe Medical (Blue Alert)",
        "url": "https://petharbor.com/results.asp?WHERE=type_DOG&PAGE=1&searchtype=ALL&friends=1&samaritans=1&nosuccess=0&rows=50&imght=120&imgres=thumb&view=sysadm.v_lact_alert_blue&start=3&SHELTERLIST=%27LACT%27,%27LACT1%27,%27LACT4%27,%27LACT3%27,%27LACT2%27,%27LACT5%27,%27LACT6%27&OrderBy=shelter",
    },
]

SHELTER_SEO_CONFIG = [
    {
        "slug": "oakland-animal-services",
        "county": "Alameda",
        "title": "Oakland Animal Services Urgent Dogs & Exit Deadlines | UrgentPaws",
        "h1": "Oakland Animal Services (OAS) Urgent Dogs",
        "meta_desc": "Live list of urgent and rescue-needed dogs at Oakland Animal Services (OAS). Updated every 3 hours with exit deadlines, behavioral notes, and Animal IDs.",
        "address": "1101 29th Ave, Oakland, CA 94601",
        "contact": "Rescue/Transfer: oastransfers@oaklandca.gov | Foster: oasfoster@oaklandca.gov",
        "intro": "Dogs currently listed on Oakland Animal Services' Rescue Needed and urgent priority boards. Include the dog's Animal ID when emailing OAS for a rescue pull, foster hold, or adoption inquiry.",
    },
    {
        "slug": "contra-costa-animal-services",
        "county": "Contra Costa",
        "title": "Contra Costa Animal Services Urgent & Transfer Preferred Dogs | UrgentPaws",
        "h1": "Contra Costa County Animal Services Urgent Dogs",
        "meta_desc": "Live tracker for urgent and Transfer Preferred dogs at Contra Costa Animal Services in Martinez, CA. Updated every 3 hours with Animal IDs and shelter links.",
        "address": "4800 Imhoff Pl, Martinez, CA 94553",
        "contact": "Rescue/Transfer: asdrescue@asd.cccounty.us | Phone: (925) 608-8400",
        "intro": "At-risk and Transfer Preferred dogs at Contra Costa County Animal Services (CCASD) in Martinez needing 501(c)(3) rescue placement, foster homes, or immediate adoption.",
    },
    {
        "slug": "san-jose-animal-care",
        "county": "Santa Clara",
        "title": "San Jose Animal Care Center Urgent & Rescue Priority Dogs | UrgentPaws",
        "h1": "San Jose Animal Care Center (SJACS) Urgent Dogs",
        "meta_desc": "Live feed of staff-flagged Rescue Priority and Foster Priority dogs at San Jose Animal Care Center. Updated every 3 hours with kennel numbers and Animal IDs.",
        "address": "2750 Monterey Rd, San Jose, CA 95111",
        "contact": "Rescue/Transfer: SJACSRescue@sanjoseca.gov | Phone: (408) 794-7297",
        "intro": "Dogs flagged by San Jose Animal Care & Services staff for urgent rescue transfer or foster placement. Reference the Animal ID and kennel number when contacting SJACS.",
    },
    {
        "slug": "stanislaus-animal-services",
        "county": "Stanislaus",
        "title": "Stanislaus Animal Services RED List & Final Call Dogs | UrgentPaws",
        "h1": "Stanislaus Animal Services (Modesto) RED & Final Call Dogs",
        "meta_desc": "Live list of RED status and Final Call dogs at Stanislaus Animal Services in Modesto, CA. Updated every 3 hours with Tuesday/Wednesday exit deadlines and IDs.",
        "address": "3647 Cornucopia Way, Modesto, CA 95358",
        "contact": "Rescue/Transfer: rescue@stancounty.com | Phone: (209) 558-7387",
        "intro": "Kennel dogs currently on Stanislaus Animal Services' RED at-risk list and Final Call deadlines in Modesto. Final Call dogs generally require rescue or adopter commitment by Tuesday COB / Wednesday 6:30 AM.",
    },
    {
        "slug": "la-animal-services",
        "county": "Los Angeles",
        "title": "LA Animal Services Euthanasia & Urgent Alert Dogs | UrgentPaws",
        "h1": "LA Animal Services (LAAS) Urgent & Medical Alert Dogs",
        "meta_desc": "Live tracker across all 6 LA Animal Services shelters (East Valley, West Valley, West LA, South LA, Harbor, North Central) for Euthanasia, Urgent, Orange, and Blue alerts.",
        "address": "6 City Shelters across Los Angeles, CA (East Valley, West Valley, West LA, South LA, Harbor, North Central)",
        "contact": "New Hope Rescue Program: ani.newhope@lacity.org | Phone: (888) 452-7381",
        "intro": "Active dogs on LA Animal Services' New Hope Euthanasia Alert, Urgent Needs, Orange Medical, and Blue Severe Medical boards across all six Los Angeles municipal shelters.",
    },
    {
        "slug": "kern-county-animal-services",
        "county": "Kern",
        "title": "Kern County Animal Services Dogs at Greatest Risk | UrgentPaws",
        "h1": "Kern County Animal Services (Bakersfield) Greatest Risk Dogs",
        "meta_desc": "Live tracker for dogs at greatest risk of euthanasia at Kern County Animal Services in Bakersfield, CA. Updated every 3 hours with Animal IDs and rescue notes.",
        "address": "3951 Fruitvale Ave, Bakersfield, CA 93308",
        "contact": "Rescue/Transfer: rescue@kerncounty.com | Phone: (661) 868-7100",
        "intro": "Dogs currently listed on Kern County Animal Services' Animals at Greatest Risk board needing immediate 501(c)(3) rescue commitment, foster placement, or adoption.",
    },
    {
        "slug": "devore-animal-shelter",
        "county": "San Bernardino",
        "title": "Devore Animal Shelter Urgent & Rescue Only Dogs | UrgentPaws",
        "h1": "San Bernardino County - Devore Animal Shelter Urgent Dogs",
        "meta_desc": "Live tracker for urgent and Rescue Only dogs at Devore Animal Shelter in San Bernardino County, CA. Updated every 3 hours with behavioral and medical flags.",
        "address": "19777 Shelter Way, San Bernardino, CA 92407",
        "contact": "Rescue/Transfer: sbrescue@dph.sbcounty.gov | Phone: (909) 386-9820",
        "intro": "Urgent and Rescue Only dogs at San Bernardino County's Devore Animal Shelter requiring immediate rescue transfer, foster commitment, or adoption.",
    },
    {
        "slug": "riverside-county-animal-services",
        "county": "Riverside",
        "title": "Riverside County Animal Services Red List & Urgent Dogs | UrgentPaws",
        "h1": "Riverside County Animal Services (Jurupa Valley) Urgent Dogs",
        "meta_desc": "Live tracker for 48-hour Rescue Only and 72-hour Red List dogs at Riverside County Department of Animal Services in Jurupa Valley, CA.",
        "address": "6851 Van Buren Blvd, Jurupa Valley, CA 92509",
        "contact": "Rescue/Transfer: transfer@rivco.org | Phone: (951) 358-7387",
        "intro": "At-risk dogs at Riverside County Department of Animal Services (RCDAS) flagged with exit deadlines, medical urgency, or Rescue Only status.",
    },
]


def clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def extract_field(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        m = re.search(rf"{re.escape(label)}\s*:\s*([^\n\r|]+)", text, re.IGNORECASE)
        if m:
            return clean_space(m.group(1))
    return ""


def is_active_flag(val) -> bool:
    if val is None:
        return False
    s = clean_space(str(val)).lower()
    if not s:
        return False
    inactive = {
        "none", "n/a", "na", "no", "not needed", "not applicable",
        "normal", "standard", "0", "false", "null", "-", "unknown",
    }
    if s in inactive:
        return False
    if s.startswith("not ") or s.startswith("no "):
        return False
    return True


def is_deadline_expired(dog: dict, today_la) -> bool:
    """Returns True if the dog has an explicit MM/DD/YY(YY) deadline earlier than today (PT)."""
    blob = f"{dog.get('deadline_or_urgency') or ''} {dog.get('urgency_type') or ''}"
    for m in DATE_PATTERN.finditer(blob):
        month, day, year_str = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(year_str) + 2000 if len(year_str) == 2 else int(year_str)
        try:
            deadline_date = datetime(year, month, day).date()
            if deadline_date < today_la:
                return True
        except ValueError:
            continue
    return False


def is_preserved_record_stale(dog: dict, now_utc: datetime, max_days: int = MAX_PRESERVED_DAYS) -> bool:
    """Returns True if a carried-over record hasn't been verified within max_days."""
    ts_str = dog.get("last_checked") or dog.get("updated_at") or ""
    if not ts_str:
        return False
    try:
        checked_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return (now_utc - checked_dt) > timedelta(days=max_days)
    except ValueError:
        return False


def load_previous_dogs() -> list[dict]:
    if not os.path.exists("dogs.json"):
        return []
    try:
        with open("dogs.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def find_petconnect_cards_across_frames(page) -> list[dict]:
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
# 1. STANISLAUS COUNTY - RED STATUS & FINAL CALL
# ---------------------------------------------------------------------------
def scrape_stanislaus_red(page, now_iso: str) -> list[dict]:
    url = "https://www.stanislausanimalservices.com/atrisk/adopt-dogs.shtm?color=red&sort=orderByIntakeDate"
    print("[1/8] Scraping Stanislaus County RED / Final Call dogs...", flush=True)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(3000)
    except Exception as exc:
        print(f"  Stanislaus error: {exc}", flush=True)
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
        if re.search(r"\bF01\b|in a foster home", txt, re.IGNORECASE):
            continue

        aid = item["animal_id"]
        lines = [clean_space(ln) for ln in txt.splitlines() if clean_space(ln)]

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
        is_rescue_only = bool(RESCUE_ONLY_PATTERN.search(txt))
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
            "needs_type": "Rescue Pull Only" if is_rescue_only else "Rescue Pull, Foster, or Adopt",
            "transport_note": clean_space(f"{days_str}. Must exit by Tue COB / Wed 6:30am if Final Call.") if is_final_call else days_str,
            "photo_url": item.get("img") or "",
            "listing_url": href,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"  Stanislaus RED/Final Call kennel dogs saved: {len(results)}", flush=True)
    return results


# ---------------------------------------------------------------------------
# 2. OAKLAND ANIMAL SERVICES - /rescue-needed/ (RescueGroups Org 5970 API)
# ---------------------------------------------------------------------------
def fetch_oas_via_rescuegroups(now_iso: str) -> list[dict]:
    """Fetches OAS (Org 5970) urgent dogs directly from RescueGroups v5 API."""
    api_key = os.environ.get("RESCUEGROUPS_API_KEY", "").strip()
    if not api_key:
        print("  RESCUEGROUPS_API_KEY not found in environment; skipping OAS API.", flush=True)
        return []

    url = (
        "https://api.rescuegroups.org/v5/public/orgs/5970/animals/search/available/dogs"
        "?include=pictures,breeds&limit=100"
    )
    headers = {
        "Content-Type": "application/vnd.api+json",
        "Authorization": api_key,
        "User-Agent": USER_AGENT,
    }

    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        print(f"  OAS RescueGroups API error: {exc}", flush=True)
        return []

    pictures_by_id = {}
    breeds_by_id = {}
    for inc in payload.get("included", []):
        inc_type = inc.get("type")
        inc_id = str(inc.get("id") or "")
        attrs = inc.get("attributes") or {}
        if inc_type == "pictures":
            pic_url = (
                (attrs.get("original") or {}).get("url")
                or (attrs.get("large") or {}).get("url")
                or (attrs.get("small") or {}).get("url")
                or ""
            )
            if inc_id and pic_url:
                pictures_by_id[inc_id] = pic_url
        elif inc_type == "breeds":
            b_name = clean_space(attrs.get("name") or "")
            if inc_id and b_name:
                breeds_by_id[inc_id] = b_name

    results = []
    for item in payload.get("data", []):
        attrs = item.get("attributes") or {}
        rels = item.get("relationships") or {}

        rg_id = str(item.get("id") or "").strip()
        aid = clean_space(str(attrs.get("rescueId") or rg_id))
        name = clean_space(attrs.get("name") or f"OAS #{aid}")

        priority_raw = str(attrs.get("priority") or "").strip()
        kill_date_raw = str(attrs.get("killDate") or attrs.get("reviewDate") or "").strip()

        deadline_str = ""
        if kill_date_raw and not kill_date_raw.startswith("2500"):
            m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", kill_date_raw)
            if m_iso:
                deadline_str = f"{int(m_iso.group(2))}/{int(m_iso.group(3))}/{m_iso.group(1)}"

        # Only include dogs flagged with a deadline or priority level on OAS's urgent board
        is_urgent = bool(deadline_str) or (priority_raw not in ("", "0", "0.0", "None", "null"))
        if not is_urgent:
            continue

        if deadline_str:
            badge = f"DEADLINE {deadline_str}"
            urgency_type = f"OAS Exit Deadline ({deadline_str})"
        else:
            badge = "OAS PRIORITY"
            urgency_type = "OAS Priority Urgent Dog"

        pic_refs = (rels.get("pictures") or {}).get("data") or []
        photo_url = next((pictures_by_id[str(p["id"])] for p in pic_refs if str(p.get("id")) in pictures_by_id), "")

        breed_refs = (rels.get("breeds") or {}).get("data") or []
        breed_names = [breeds_by_id[str(b["id"])] for b in breed_refs if str(b.get("id")) in breeds_by_id]
        breed = clean_space(attrs.get("breedString") or " / ".join(breed_names))

        age = clean_space(attrs.get("ageGroup") or attrs.get("ageString") or "")
        size = clean_space(attrs.get("sizeGroup") or "")
        sex = clean_space(attrs.get("sex") or "")
        desc_plain = clean_space(html.unescape(re.sub(r"<[^>]+>", " ", attrs.get("descriptionText") or attrs.get("descriptionHtml") or "")))
        is_rescue_only = bool(RESCUE_ONLY_PATTERN.search(desc_plain))

        meta_bits = [b for b in [age, sex, breed] if b]
        listing_id = rg_id or aid
        listing_url = f"https://www.oaklandanimalservices.org/rescue-needed/animal?animalId={listing_id}"

        results.append({
            "id": f"oas-{listing_id}",
            "animal_id": listing_id,
            "name": name,
            "breed": breed,
            "shelter": "Oakland Animal Services",
            "city": "Oakland",
            "county": "Alameda",
            "state": "CA",
            "size": size,
            "age": age,
            "urgency_type": urgency_type,
            "deadline_or_urgency": badge,
            "needs_type": "Rescue Pull Only" if is_rescue_only else "Rescue Pull, Foster, or Adopt",
            "transport_note": ", ".join(meta_bits)[:140],
            "photo_url": photo_url,
            "listing_url": listing_url,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"  OAS RescueGroups API urgent dogs saved: {len(results)}", flush=True)
    return results


def scrape_oas_rescue_needed(page, now_iso: str) -> list[dict]:
    """Entry point called by main() at [2/8]—uses RescueGroups API exclusively for OAS."""
    print("[2/8] Fetching Oakland Animal Services via RescueGroups API (Org 5970)...", flush=True)
    results = fetch_oas_via_rescuegroups(now_iso)
    if not results:
        print("  No new OAS records returned; preserving non-expired Alameda records from dogs.json.", flush=True)
    return results


# ---------------------------------------------------------------------------
# 3-6. 24PETCONNECT MUNICIPAL URGENT BOARDS
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
    print(f"{step_label} Scraping {shelter_name} ({url})...", flush=True)
    try:
        page.goto(url, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(4500)
    except Exception as exc:
        print(f"  Load error ({shelter_name}): {exc}", flush=True)
        return []

    raw_cards = find_petconnect_cards_across_frames(page)
    print(f"  Detected {len(raw_cards)} raw cards on {shelter_name}", flush=True)

    results = []
    seen_ids = set()

    for c in raw_cards:
        txt = c.get("text") or ""
        href = (c.get("href") or "").split("?")[0]

        if NON_DOG_PATTERN.search(txt) or RESCUED_PATTERN.search(txt):
            continue

        id_match = re.search(r"\b(A\d{5,8})\b", txt) or re.search(r"/(A\d{5,8})\b", href)
        animal_id = id_match.group(1).upper() if id_match else ""
        if not animal_id or animal_id in seen_ids:
            continue

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

        if "CCASDTransferPreferredPets" in url:
            href = f"https://24petconnect.com/CCASDTransferPreferredPets/Details/CCST/{animal_id}"
        elif "CCASDAvailablePets" in url:
            href = f"https://24petconnect.com/CCASDAvailablePets/Details/CCST/{animal_id}"
        elif portal_code == "KernCountyRescue" or "KernCountyRescue/Details" in href or not href:
            code_for_link = "KERN" if portal_code == "KernCountyRescue" else portal_code
            href = f"https://24petconnect.com/DetailsMain/{code_for_link}/{animal_id}"

        urgency_type = default_urgency
        badge = default_badge
        note = reason
        is_rescue_only = bool(RESCUE_ONLY_PATTERN.search(status + " " + txt + " " + default_badge))
        needs = "Rescue Pull Only" if is_rescue_only else "Rescue Pull, Foster, or Adopt"

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

    if inspect_details_for_deadline and results:
        verified = []
        for dog in results[:35]:
            try:
                page.goto(dog["listing_url"], wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(800)
                body = page.inner_text("body")
            except Exception:
                continue

            if RESCUED_PATTERN.search(body) or NON_DOG_PATTERN.search(body[:600]):
                continue

            is_at_risk = bool(re.search(r"at risk animal|deadline|must exit|rescue only|extremely urgent|parvo|medical", body, re.IGNORECASE))
            if not is_at_risk:
                continue

            if RESCUE_ONLY_PATTERN.search(body):
                dog["needs_type"] = "Rescue Pull Only"

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

    print(f"  {shelter_name} urgent dogs saved: {len(results)}", flush=True)
    return results


# ---------------------------------------------------------------------------
# 7. LA ANIMAL SERVICES (6 CITY SHELTERS) - NEW HOPE ALERT BOARDS
# ---------------------------------------------------------------------------
def enrich_laas_from_petharbor(dog: dict) -> bool:
    """Enriches a single LAAS dog in parallel via laanimalservices.com or PetHarbor."""
    global LAAS_WEB_BLOCKED
    aid = (dog.get("animal_id") or "").strip()
    if not aid:
        return False

    before_name = dog.get("name")
    laas_pet_url = f"https://www.laanimalservices.com/pet/{aid.lower()}"
    petharbor_url = dog.get("listing_url") or ""

    # 1. Try laanimalservices.com/pet/<id> unless flagged as blocked by datacenter firewall
    if not LAAS_WEB_BLOCKED:
        try:
            req = urllib.request.Request(laas_pet_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=4) as resp:
                raw_html = resp.read().decode("utf-8", errors="replace")
            plain = clean_space(html.unescape(re.sub(r"<[^>]+>", " ", raw_html)))
            if plain and aid.upper() in plain.upper():
                dog["listing_url"] = laas_pet_url

                h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", raw_html, re.IGNORECASE | re.DOTALL)
                if h1_match:
                    parsed_name = clean_space(html.unescape(re.sub(r"<[^>]+>", "", h1_match.group(1))))
                    if parsed_name and parsed_name.upper() != "LAAS":
                        dog["name"] = parsed_name

                if RESCUE_ONLY_PATTERN.search(plain):
                    dog["needs_type"] = "New Hope Rescue Pull Only"
                    if "rescue organizations only" not in (dog.get("transport_note") or "").lower():
                        dog["transport_note"] = f"{dog.get('transport_note', '')} • Rescue Organizations Only".strip(" •")

                m_branch = re.search(
                    r"\b(East Valley|West Valley|West Los Angeles|West LA|South Los Angeles|South LA|Harbor|North Central)\b",
                    plain,
                    re.IGNORECASE,
                )
                if m_branch:
                    dog["shelter"] = f"LA Animal Services ({m_branch.group(1).title()})"

                m_size = re.search(r"\bSize\s+([^\n]+?)(?=\s+Color\b|\s+Breed\b)", plain, re.IGNORECASE)
                if m_size and not dog.get("size"):
                    dog["size"] = clean_space(m_size.group(1))

                m_breed = re.search(r"\bBreed\s+([^\n]+?)(?=\s+Lifestage\b|\s+Care Status\b)", plain, re.IGNORECASE)
                if m_breed and not dog.get("breed"):
                    dog["breed"] = clean_space(m_breed.group(1))

                m_stage = re.search(r"\bLifestage\s+([^\n]+?)(?=\s+Care Status\b|\s+Medical\b)", plain, re.IGNORECASE)
                if m_stage and not dog.get("age"):
                    dog["age"] = clean_space(m_stage.group(1))

                return dog.get("name") != before_name
        except Exception:
            LAAS_WEB_BLOCKED = True

    # 2. Fast PetHarbor detail fallback (always reachable from GitHub Actions)
    if "petharbor.com/detail.asp" not in petharbor_url.lower():
        return False
    try:
        req = urllib.request.Request(petharbor_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=6) as resp:
            ph_html = resp.read().decode("utf-8", errors="replace")
        ph_plain = clean_space(html.unescape(re.sub(r"<[^>]+>", " ", ph_html)))
    except Exception:
        return False

    m_title = re.search(rf"\b([A-Za-z][A-Za-z0-9' .-]{{1,30}}?)\s*-\s*ID\s*#?\s*{re.escape(aid)}\b", ph_plain, re.IGNORECASE)
    m_intro = re.search(r"\bMy name is\s+([A-Za-z][A-Za-z0-9' -]{1,30}?)\s+and I am\b", ph_plain, re.IGNORECASE)
    parsed_name = (m_title.group(1) if m_title else (m_intro.group(1) if m_intro else "")).strip(" -–|:")
    if parsed_name and parsed_name.upper() != "LAAS":
        dog["name"] = parsed_name.title() if parsed_name.isupper() else parsed_name

    m_branch = re.search(r"Los Angeles Animal Services\s*-\s*([A-Za-z ]+?Shelter)\b", ph_plain, re.IGNORECASE)
    if m_branch:
        branch = clean_space(m_branch.group(1)).replace(" Shelter", "").strip()
        if branch:
            dog["shelter"] = f"LA Animal Services ({branch})"

    if not dog.get("age"):
        m_age = re.search(r"\babout\s+(\d+\s+(?:years?|months?|weeks?)\s+old)\b", ph_plain, re.IGNORECASE)
        if m_age:
            dog["age"] = clean_space(m_age.group(1))

    if not dog.get("size"):
        m_wt = re.search(r"\bapproximately\s+(\d+)\s+pounds?\b", ph_plain, re.IGNORECASE)
        if m_wt:
            dog["size"] = f"{m_wt.group(1)} lbs"

    if not dog.get("breed"):
        m_breed = re.search(r"\bMy name is\s+[^.]+?\s+and I am (?:a|an)\s+([^.]+)\.", ph_plain, re.IGNORECASE)
        if m_breed:
            dog["breed"] = clean_space(m_breed.group(1))

    if RESCUE_ONLY_PATTERN.search(ph_plain):
        dog["needs_type"] = "New Hope Rescue Pull Only"

    return dog.get("name") != before_name


def scrape_laas_newhope_alerts(page, now_iso: str) -> list[dict]:
    print("[7/8] Scraping LA Animal Services New Hope Alert boards (Euth/Urgent/Orange/Blue)...", flush=True)
    results = []
    seen_ids = set()

    for board in LAAS_ALERT_BOARDS:
        try:
            page.goto(board["url"], wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"  {board['label']} error: {exc}", flush=True)
            continue

        rows = page.evaluate(
            """() => {
                const out = [];
                const trs = Array.from(document.querySelectorAll("table tr"));
                for (const tr of trs) {
                    const link = tr.querySelector("a[href*='detail.asp'], a[href*='Details']");
                    if (!link) continue;
                    const tds = Array.from(tr.querySelectorAll("td")).map(td => (td.innerText || "").trim());
                    const img = tr.querySelector("img");
                    out.push({
                        href: link.href || "",
                        img: img ? img.src : "",
                        cells: tds,
                        text: (tr.innerText || "").trim()
                    });
                }
                return out;
            }"""
        )

        if not rows:
            cards = find_petconnect_cards_across_frames(page)
            for c in cards:
                rows.append({"href": c.get("href", ""), "img": c.get("img", ""), "cells": [], "text": c.get("text", "")})

        board_count = 0
        for r in rows:
            txt = r.get("text") or ""
            href = r.get("href") or ""
            if NON_DOG_PATTERN.search(txt) or RESCUED_PATTERN.search(txt):
                continue

            id_match = re.search(r"\b(A\d{6,8})\b", href + " " + txt, re.IGNORECASE)
            animal_id = id_match.group(1).upper() if id_match else ""
            if not animal_id or animal_id in seen_ids:
                continue
            seen_ids.add(animal_id)

            cells = [clean_space(c) for c in (r.get("cells") or []) if clean_space(c)]
            name = ""
            breed = ""
            age = ""
            shelter_loc = "LA Animal Services"

            if cells:
                name_clean = re.sub(r"\(?\bA\d{6,8}\b\)?", "", cells[0]).strip(" -()")
                if name_clean and len(name_clean) < 35 and name_clean.upper() != "LAAS":
                    name = name_clean
                for cell in cells[1:]:
                    if any(s in cell.lower() for s in ("east valley", "west valley", "west la", "south la", "harbor", "north central", "chesterfield", "shelter")):
                        shelter_loc = f"LA Animal Services ({cell})"
                    elif re.search(r"\b\d+\s*(yr|year|mo|month|wk|week)", cell, re.IGNORECASE) and not age:
                        age = cell
                    elif any(b in cell.lower() for b in ("pit", "terrier", "shepherd", "husky", "mix", "retriever", "chihuahua", "hound", "rottweiler", "bulldog", "boxer", "poodle", "malinois", "doberman")) and not breed:
                        breed = cell

            if not name:
                name = extract_field(txt, ("Meet", "Name")) or f"LAAS {animal_id}"

            results.append({
                "id": f"laas-{animal_id.lower()}",
                "animal_id": animal_id,
                "name": name,
                "breed": breed,
                "shelter": shelter_loc,
                "city": "Los Angeles",
                "county": "Los Angeles",
                "state": "CA",
                "size": "",
                "age": age,
                "urgency_type": board["urgency_type"],
                "deadline_or_urgency": board["badge"],
                "needs_type": "New Hope Rescue Pull Only" if RESCUE_ONLY_PATTERN.search(txt) else "New Hope Rescue Pull / Foster / Adopt",
                "transport_note": f"Active on {board['label']}",
                "photo_url": r.get("img") or "",
                "listing_url": href or f"https://www.laanimalservices.com/pet/{animal_id.lower()}",
                "updated_at": now_iso,
                "last_checked": now_iso,
            })
            board_count += 1

        print(f"  {board['label']}: {board_count} dogs", flush=True)

    with ThreadPoolExecutor(max_workers=12) as pool:
        enriched_count = sum(1 for changed in pool.map(enrich_laas_from_petharbor, results) if changed)

    print(f"  LAAS total alert dogs saved: {len(results)} (resolved {enriched_count} names from official profiles)", flush=True)
    return results


# ---------------------------------------------------------------------------
# 8. SAN JOSE ACS - ARCGIS REST JSON API (Staff-Flagged Rescue Priority Only)
# ---------------------------------------------------------------------------
def fetch_sanjose_arcgis(now_iso: str) -> list[dict]:
    print("[8/8] Querying San Jose ACS ArcGIS MapServer Layer 1 (Strict Filter)...", flush=True)
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
        print(f"  San Jose ArcGIS API error: {exc}", flush=True)
        return []

    features = data.get("features", [])
    results = []
    for feat in features:
        attrs = feat.get("attributes", {})

        rescue_status = clean_space(str(attrs.get("RESCUESTATUS") or ""))
        rescue_priority = clean_space(str(attrs.get("RESCUEPRIORITY") or ""))
        foster_priority = clean_space(str(attrs.get("FOSTERPRIORITY") or ""))
        animal_status = clean_space(str(attrs.get("ANIMALSTATUS") or ""))

        try:
            days_in = int(float(attrs.get("DAYSINSHELTER") or 0))
        except (TypeError, ValueError):
            days_in = 0

        has_rescue_priority = is_active_flag(rescue_priority)
        has_rescue_status = is_active_flag(rescue_status)
        has_foster_priority = is_active_flag(foster_priority)

        if not (has_rescue_priority or has_rescue_status or has_foster_priority):
            continue

        aid = clean_space(str(attrs.get("ANIMALID") or attrs.get("OBJECTID") or ""))
        name = clean_space(str(attrs.get("PETNAME") or f"San Jose {aid}"))
        breed = clean_space(str(attrs.get("BREED") or ""))
        age = clean_space(str(attrs.get("PETAGE") or ""))
        size = clean_space(str(attrs.get("PETSIZE") or ""))
        weight = clean_space(str(attrs.get("PETWEIGHT") or ""))
        if weight and not size:
            size = f"{weight} lbs"

        photo_url = clean_space(str(attrs.get("PHOTOLINK") or ""))
        direct_url = clean_space(str(attrs.get("WEBLINK") or "")) or pet_compass_url

        combined_flags = f"{rescue_priority} {rescue_status} {animal_status}"
        is_rescue_only = bool(RESCUE_ONLY_PATTERN.search(combined_flags))

        if has_rescue_priority:
            badge = rescue_priority[:28]
            urgency_type = f"SJ ACS {rescue_priority}"
            needs = "Rescue Pull Only" if is_rescue_only else "Rescue Pull, Foster, or Adopt"
        elif has_rescue_status:
            badge = f"SJ Rescue: {rescue_status[:24]}"
            urgency_type = f"SJ ACS Rescue Needed ({rescue_status})"
            needs = "Rescue Pull Only" if is_rescue_only else "Rescue Pull, Foster, or Adopt"
        else:
            badge = f"SJ Foster: {foster_priority[:24]}"
            urgency_type = f"SJ ACS Foster Priority ({foster_priority})"
            needs = "Urgent Foster / Adopt"

        kennel = clean_space(str(attrs.get("KENNELNAME") or ""))
        note_parts = [p for p in [f"{days_in} days in shelter" if days_in else "", f"Kennel {kennel}" if kennel else "", animal_status] if p]

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
            "urgency_type": urgency_type,
            "deadline_or_urgency": badge,
            "needs_type": needs,
            "transport_note": " | ".join(note_parts),
            "photo_url": photo_url,
            "listing_url": direct_url,
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"  San Jose ArcGIS strict urgent/priority dogs saved: {len(results)}", flush=True)
    return results


# ---------------------------------------------------------------------------
# 9. GLOBAL STANDARDIZATION & STATIC SHELTER SEO PAGES
# ---------------------------------------------------------------------------
def normalize_dog_record(dog: dict) -> dict:
    """Ensures needs_type and metadata formatting are consistent across all 8 shelters."""
    combined = " ".join(
        str(dog.get(k) or "")
        for k in ("needs_type", "urgency_type", "deadline_or_urgency", "transport_note")
    )
    is_laas = "los angeles" in (dog.get("county") or "").lower()
    if RESCUE_ONLY_PATTERN.search(combined):
        dog["needs_type"] = "New Hope Rescue Pull Only" if is_laas else "Rescue Pull Only"
    elif not dog.get("needs_type"):
        dog["needs_type"] = "New Hope Rescue Pull / Foster / Adopt" if is_laas else "Rescue Pull, Foster, or Adopt"
    return dog


def generate_static_seo_pages(unique_dogs: list[dict], now_iso: str) -> None:
    print("Generating static shelter HTML pages and sitemap.xml for SEO...", flush=True)
    base_url = "https://www.urgentpaws.org"
    today_date = now_iso[:10]

    nav_links = [f'<a href="/{cfg["slug"]}">{html.escape(cfg["h1"].replace(" Urgent Dogs", ""))}</a>' for cfg in SHELTER_SEO_CONFIG]
    nav_html = " &middot; ".join(nav_links)

    for cfg in SHELTER_SEO_CONFIG:
        slug = cfg["slug"]
        county = cfg["county"]
        page_url = f"{base_url}/{slug}"
        shelter_dogs = [d for d in unique_dogs if (d.get("county") or "").lower() == county.lower()]

        cards_html = []
        ld_items = []

        for idx, dog in enumerate(shelter_dogs, start=1):
            d_name = html.escape(dog.get("name") or f"Dog {dog.get('animal_id', '')}")
            d_id = html.escape(dog.get("animal_id") or "")
            d_breed = html.escape(dog.get("breed") or "Mixed Breed")
            d_age = html.escape(dog.get("age") or "")
            d_size = html.escape(dog.get("size") or "")
            d_badge = html.escape(dog.get("deadline_or_urgency") or "Urgent")
            d_needs = html.escape(dog.get("needs_type") or "")
            d_shelter = html.escape(dog.get("shelter") or cfg["h1"])
            d_note = html.escape(dog.get("transport_note") or "")
            d_url = html.escape(dog.get("listing_url") or base_url)
            d_photo = html.escape(dog.get("photo_url") or "")

            meta_bits = [bit for bit in [d_breed, d_age, d_size] if bit]
            meta_str = " &bull; ".join(meta_bits)

            img_tag = (
                f'<img src="{d_photo}" alt="{d_name} ({d_id}) at {d_shelter}" loading="lazy">'
                if d_photo
                else '<div class="no-photo">Photo on Shelter Listing</div>'
            )

            cards_html.append(
                f"""<article class="card">
  {img_tag}
  <div class="card-body">
    <span class="badge">{d_badge}</span>
    <h2>{d_name} <span class="aid">#{d_id}</span></h2>
    <p class="shelter-loc">{d_shelter}</p>
    <p class="meta">{meta_str}</p>
    {f'<p class="needs">{d_needs}</p>' if d_needs else ''}
    {f'<p class="note">{d_note}</p>' if d_note else ''}
    <a class="btn" href="{d_url}" target="_blank" rel="noopener noreferrer">View Official Shelter Listing &rarr;</a>
  </div>
</article>"""
            )

            ld_items.append({
                "@type": "ListItem",
                "position": idx,
                "name": f"{dog.get('name', 'Urgent Dog')} ({dog.get('animal_id', '')}) - {dog.get('deadline_or_urgency', 'Urgent')}",
                "url": dog.get("listing_url") or page_url,
            })

        if not cards_html:
            cards_section = (
                '<p class="empty">No dogs are currently listed on this urgent feed, or the shelter board is between updates. '
                'Check back shortly or browse all California shelters on the <a href="/">UrgentPaws main directory</a>.</p>'
            )
        else:
            cards_section = f'<section class="grid">{"".join(cards_html)}</section>'

        json_ld = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": cfg["title"],
                "description": cfg["meta_desc"],
                "url": page_url,
                "dateModified": now_iso,
                "mainEntity": {
                    "@type": "ItemList",
                    "numberOfItems": len(shelter_dogs),
                    "itemListElement": ld_items,
                },
            },
            ensure_ascii=False,
        )

        page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(cfg["title"])}</title>
  <meta name="description" content="{html.escape(cfg["meta_desc"])}">
  <link rel="canonical" href="{page_url}">
  <script type="application/ld+json">{json_ld}</script>
  <style>
    :root {{ --bg: #f8fafc; --card: #ffffff; --text: #0f172a; --muted: #475569; --accent: #dc2626; --border: #e2e8f0; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; }}
    header {{ background: #0f172a; color: #fff; padding: 1.25rem 1rem; }}
    .wrap {{ max-width: 1120px; margin: 0 auto; padding: 0 1rem; }}
    header a {{ color: #93c5fd; text-decoration: none; font-weight: 600; }}
    h1 {{ margin: 0.5rem 0 0.25rem; font-size: 1.65rem; }}
    .sub {{ color: #cbd5e1; margin: 0; font-size: 0.95rem; }}
    .info-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 1rem 1.25rem; margin: 1.25rem 0; }}
    .info-box p {{ margin: 0.35rem 0; font-size: 0.95rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1.15rem; margin: 1.25rem 0 2rem; }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; }}
    .card img {{ width: 100%; height: 240px; object-fit: cover; object-position: center 20%; background: #e2e8f0; }}
    .no-photo {{ height: 240px; display: flex; align-items: center; justify-content: center; background: #e2e8f0; color: var(--muted); font-size: 0.9rem; }}
    .card-body {{ padding: 1rem; display: flex; flex-direction: column; flex: 1; }}
    .badge {{ display: inline-block; align-self: flex-start; background: #fee2e2; color: #991b1b; font-weight: 700; font-size: 0.75rem; padding: 0.2rem 0.55rem; border-radius: 999px; margin-bottom: 0.5rem; }}
    .card h2 {{ margin: 0 0 0.25rem; font-size: 1.2rem; }}
    .aid {{ color: var(--muted); font-weight: 500; font-size: 0.95rem; }}
    .shelter-loc {{ margin: 0 0 0.25rem; font-size: 0.88rem; color: var(--muted); }}
    .meta {{ margin: 0 0 0.4rem; font-size: 0.9rem; font-weight: 500; }}
    .needs {{ margin: 0 0 0.5rem; font-size: 0.82rem; font-weight: 700; color: #b91c1c; }}
    .note {{ margin: 0 0 0.85rem; font-size: 0.88rem; color: var(--muted); }}
    .btn {{ margin-top: auto; display: inline-block; text-align: center; background: #0f172a; color: #fff; text-decoration: none; padding: 0.6rem 0.9rem; border-radius: 6px; font-weight: 600; font-size: 0.9rem; }}
    .btn:hover {{ background: #1e293b; }}
    footer {{ border-top: 1px solid var(--border); padding: 1.5rem 0 2.5rem; font-size: 0.9rem; color: var(--muted); }}
    footer a {{ color: #1d4ed8; text-decoration: none; }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <a href="/">&larr; All California Urgent Shelter Dogs (UrgentPaws)</a>
      <h1>{html.escape(cfg["h1"])} ({len(shelter_dogs)})</h1>
      <p class="sub">Auto-updated every 3 hours &bull; Last checked: {html.escape(now_iso)}</p>
    </div>
  </header>
  <main class="wrap">
    <section class="info-box">
      <p>{html.escape(cfg["intro"])}</p>
      <p><strong>Shelter Address:</strong> {html.escape(cfg["address"])}</p>
      <p><strong>Contact:</strong> {html.escape(cfg["contact"])}</p>
    </section>
    {cards_section}
  </main>
  <footer>
    <div class="wrap">
      <p><strong>Browse Other California Shelters:</strong> {nav_html}</p>
      <p><a href="/">Main UrgentPaws Directory</a> &middot; <a href="/about-urgent-paws">About UrgentPaws</a></p>
    </div>
  </footer>
</body>
</html>
"""
        with open(f"{slug}.html", "w", encoding="utf-8") as f:
            f.write(page_html)

    static_pages = [
        ("", "hourly", "1.0"),
        ("/about-urgent-paws", "weekly", "0.6"),
        ("/for-shelters", "weekly", "0.6"),
        ("/blog", "weekly", "0.6"),
        ("/contact-us", "monthly", "0.5"),
    ]

    sitemap_urls = [
        f"""  <url>
    <loc>{base_url}{path or "/"}</loc>
    <lastmod>{today_date}</lastmod>
    <changefreq>{freq}</changefreq>
    <priority>{prio}</priority>
  </url>"""
        for path, freq, prio in static_pages
    ]

    for cfg in SHELTER_SEO_CONFIG:
        sitemap_urls.append(
            f"""  <url>
    <loc>{base_url}/{cfg["slug"]}</loc>
    <lastmod>{today_date}</lastmod>
    <changefreq>hourly</changefreq>
    <priority>0.85</priority>
  </url>"""
        )

    sitemap_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(sitemap_urls)
        + "\n</urlset>\n"
    )
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write(sitemap_xml)

    print(f"  Generated {len(SHELTER_SEO_CONFIG)} static shelter pages + sitemap.xml", flush=True)


def main():
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    today_la = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    only_oas = "--only-oas" in sys.argv
    previous_dogs = load_previous_dogs()
    all_dogs = []

    if only_oas:
        print("Running in --only-oas mode (preserving non-expired records for other counties)...", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 1800})
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        if only_oas:
            all_dogs.extend(scrape_oas_rescue_needed(page, now_iso))
        else:
            all_dogs.extend(scrape_stanislaus_red(page, now_iso))
            all_dogs.extend(scrape_oas_rescue_needed(page, now_iso))
            all_dogs.extend(
                scrape_24petconnect_board(
                    page=page,
                    now_iso=now_iso,
                    step_label="[3/8]",
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
                    step_label="[4/8]",
                    url="https://24petconnect.com/CCASDTransferPreferredPets?at=DOG&sb=days_MaxMin",
                    shelter_name="Contra Costa Animal Services - Martinez",
                    city="Martinez",
                    county="Contra Costa",
                    portal_code="CCST",
                    require_urgent_flag=False,
                    default_badge="Transfer Preferred",
                    default_urgency="Transfer Preferred / Rescue Priority",
                )
            )
            all_dogs.extend(
                scrape_24petconnect_board(
                    page=page,
                    now_iso=now_iso,
                    step_label="[4b/8]",
                    url="https://24petconnect.com/CCASDAvailablePets?at=Dog&sb=days_MaxMin",
                    shelter_name="Contra Costa Animal Services - Martinez (Urgent)",
                    city="Martinez",
                    county="Contra Costa",
                    portal_code="CCST",
                    require_urgent_flag=True,
                    default_badge="Urgent / At Risk",
                    default_urgency="Urgent Shelter Dog",
                )
            )
            all_dogs.extend(
                scrape_24petconnect_board(
                    page=page,
                    now_iso=now_iso,
                    step_label="[5/8]",
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
                    step_label="[6/8]",
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
                    step_label="[6b/8]",
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
            all_dogs.extend(scrape_laas_newhope_alerts(page, now_iso))

        browser.close()

    if not only_oas:
        all_dogs.extend(fetch_sanjose_arcgis(now_iso))

    # Preserve previous county records if a shelter site returned 0 (or in --only-oas mode),
    # skipping anything older than MAX_PRESERVED_DAYS so unrefreshed feeds don't linger forever.
    active_counties = {d.get("county") for d in all_dogs if d.get("county")}
    stale_dropped = 0
    for prev in previous_dogs:
        c = prev.get("county")
        if c and c not in active_counties:
            if not only_oas and is_preserved_record_stale(prev, now_utc):
                stale_dropped += 1
                continue
            if c == "Contra Costa" and prev.get("animal_id"):
                aid = prev["animal_id"].upper()
                prev["id"] = f"24pc-ccst-{aid.lower()}"
                prev["listing_url"] = f"https://24petconnect.com/CCASDTransferPreferredPets/Details/CCST/{aid}"
            all_dogs.append(prev)

    seen = set()
    unique_dogs = []
    expired_dropped = 0
    for dog in all_dogs:
        if is_deadline_expired(dog, today_la):
            expired_dropped += 1
            continue
        key = f"{dog.get('county')}-{dog.get('animal_id')}".lower()
        if key not in seen:
            seen.add(key)
            unique_dogs.append(normalize_dog_record(dog))

    if expired_dropped or stale_dropped:
        print(
            f"  Pruned {expired_dropped} passed-deadline dogs and {stale_dropped} stale (>7d) preserved dogs.",
            flush=True,
        )

    def sort_rank(d: dict) -> tuple:
        badge = (d.get("deadline_or_urgency") or "").upper()
        if any(k in badge for k in ("EUTHANASIA", "FINAL CALL", "DEADLINE", "OUT OF TIME", "NEEDS RESCUE FINAL")):
            tier = 0
        elif any(k in badge for k in ("URGENT", "RESCUE", "RED", "ALERT", "PRIORITY")):
            tier = 1
        elif any(k in badge for k in ("GREATEST RISK", "TRANSFER")):
            tier = 2
        else:
            tier = 3
        return (tier, d.get("county", ""), d.get("name", ""))

    unique_dogs.sort(key=sort_rank)

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(unique_dogs, f, indent=2)
        f.write("\n")

    generate_static_seo_pages(unique_dogs, now_iso)
    print(f"TOTAL VERIFIED MUNICIPAL URGENT DOGS SAVED: {len(unique_dogs)}", flush=True)


if __name__ == "__main__":
    main()
