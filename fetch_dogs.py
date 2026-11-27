import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import html
import json
import os
import re
import urllib.request
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

NON_DOG_PATTERN = re.compile(
    r"\b(animal type:\s*(cat|feline|bird|rabbit|other)|cat|feline|domestic\s*(?:sh|mh|lh|short|medium|long)(?:hair)?|siamese|calico|tabby|tortoiseshell|maine\s*coon|ragdoll|bengal|persian|kitten|chicken|pigeon|rabbit|bunny|guinea pig|hamster|ferret)\b",
    re.IGNORECASE,
)

RESCUED_PATTERN = re.compile(
    r"\b(i have been rescued|commitment rcvd|adopted|rescue hold|pending pickup|currently on hold for them|rescue partner is planning to take me)\b",
    re.IGNORECASE,
)

RESCUE_ONLY_PATTERN = re.compile(
    r"\b(rescue\s*only|rescue\s*pull\s*only|available\s+to\s+rescue\s+organizations?\s+only|501\s*\(c\)\s*\(3\)\s*only|resq-bhv|resq|ap-khoa)\b",
    re.IGNORECASE,
)

BOILERPLATE_PATTERN = re.compile(
    r"\b(featur(?:e|ed|ing)\s+a\s+pet|sponsor|donate|click\s+here|http|www\.|learn\s+more|subscribe|newsletter)\b",
    re.IGNORECASE,
)

NAME_NOISE_RE = re.compile(
    r"^(?:dog|canine|male|female|neutered|spayed|intact|altered|unknown|available|stray\s*wait|owner\s*surrender|confiscate|quarantine|hold|web|foster|adoptable|rescue\s*only|ap-khoa|resq[a-z0-9-]*|medical|urgent|tustin|downey|lancaster|palmdale|baldwin\s*park|carson|gardena|agoura|castaic|santa\s*clarita\s*valley|oc\s*animal\s*care|la\s*county\s*dacc)$",
    re.IGNORECASE,
)

DATE_PATTERN = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")

SBCT_DEADLINE_RE = re.compile(
    r"Needs Placement By:\s*(\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)
SBCT_RESCUE_ONLY_RE = re.compile(
    r"only be placed with an?\s+(?:approved\s+501\(c\)\(3\)\s+rescue\s+organization|rescue\s+partner)(?:\s+due to my\s+([a-z\s]+?)\s+needs)?",
    re.IGNORECASE,
)
SBCT_FOSTER_RE = re.compile(
    r"looking for a foster home or rescue partner",
    re.IGNORECASE,
)

DACC_CENTER_EMAILS = {
    "downey": "DACCDowneyRescue@animalcare.lacounty.gov",
    "baldwin park": "DACCBaldwinParkRescue@animalcare.lacounty.gov",
    "carson": "DACCCarsonRescue@animalcare.lacounty.gov",
    "gardena": "DACCCarsonRescue@animalcare.lacounty.gov",
    "lancaster": "DACCLancasterRescue@animalcare.lacounty.gov",
    "palmdale": "DACCPalmdaleRescue@animalcare.lacounty.gov",
    "agoura": "DACCAgouraRescue@animalcare.lacounty.gov",
    "castaic": "DACCCastaicRescue@animalcare.lacounty.gov",
}

PETCONNECT_CARD_SELECTOR = (
    "a[href*='Details'], a[href*='details'], .gridResult, "
    "[class*='gridResult'], [class*='petCard']"
)

LAAS_WEB_BLOCKED = False
MAX_PRESERVED_DAYS = 7
BROWSER_PORTAL_TYPES = {
    "stanislaus",
    "24petconnect",
    "laas_petharbor",
    "oc_rescuetrack",
    "lacounty_dacc",
}

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

PROGRAMMATIC_SEO_LANDERS = [
    {
        "slug": "california-shelter-euthanasia-list",
        "name": "California Shelter Euthanasia & Red List Dogs",
        "title": "California Shelter Euthanasia List & Final Call Red List Dogs (Live) | UrgentPaws",
        "h1": "California Shelter Euthanasia List & Final Call Red List Dogs",
        "meta_desc": "Live directory of California municipal shelter dogs on euthanasia lists, Final Call deadlines, and Red List alerts across Los Angeles, Bay Area, Inland Empire, and Central Valley.",
        "intro": "Dogs with active euthanasia deadlines, Final Call exit timestamps, or Red List alerts across California municipal shelters. Contact the listed shelter rescue desk immediately with the dog's Animal ID.",
        "filter_fn": lambda d: any(
            k in (d.get("deadline_or_urgency") or "").upper()
            for k in ("EUTHANASIA", "FINAL CALL", "DEADLINE", "RED STATUS", "ALERT")
        ),
    },
    {
        "slug": "rescue-only-dogs-california",
        "name": "501(c)(3) Rescue Pull Only Dogs in California",
        "title": "Rescue Only Shelter Dogs in California (501c3 Pull, RESQ-BHV, AP-KHOA, New Hope) | UrgentPaws",
        "h1": "501(c)(3) Rescue Pull Only Shelter Dogs in California",
        "meta_desc": "Live feed of California municipal shelter dogs restricted to approved 501(c)(3) rescue partners (OC RESQ-BHV, LA County DACC Rescue Only & AP-KHOA, LAAS New Hope, SBCT).",
        "intro": "At-risk dogs designated Rescue Pull Only due to medical or behavioral holds. Approved 501(c)(3) rescue organizations can email the shelter rescue coordinator directly with the Animal ID to place a hold.",
        "filter_fn": lambda d: "rescue pull only" in (d.get("needs_type") or "").lower(),
    },
    {
        "slug": "foster-urgent-shelter-dogs-california",
        "name": "Foster-Urgent Shelter Dogs in California",
        "title": "Foster Urgent Shelter Dogs in California (Bay Area, LA & Inland Empire) | UrgentPaws",
        "h1": "Foster-Urgent Shelter Dogs in California",
        "meta_desc": "California municipal shelter dogs eligible for urgent foster placement or adoption to clear kennel space in Oakland, San Jose, Stanislaus, San Bernardino, and Los Angeles.",
        "intro": "At-risk shelter dogs eligible for temporary foster placement or public adoption. Fostering frees critical kennel capacity and immediately removes a dog from euthanasia risk.",
        "filter_fn": lambda d: "foster" in (d.get("needs_type") or "").lower(),
    },
    {
        "slug": "urgent-huskies-california",
        "name": "Urgent Huskies & Malamutes in California Shelters",
        "title": "Urgent Siberian Huskies & Malamutes on California Shelter Lists | UrgentPaws",
        "h1": "Urgent Siberian Huskies & Malamutes in California Shelters",
        "meta_desc": "Live tracker of Siberian Huskies and Alaskan Malamutes on California municipal shelter urgent lists, euthanasia deadlines, and 501(c)(3) rescue-only boards.",
        "intro": "Siberian Huskies, Alaskan Malamutes, and Husky mixes currently facing urgent deadlines or rescue-only status in California municipal shelters.",
        "filter_fn": lambda d: bool(re.search(r"\b(husky|malamute)\b", d.get("breed") or "", re.IGNORECASE)),
    },
    {
        "slug": "urgent-german-shepherds-california",
        "name": "Urgent German Shepherds & Malinois in California Shelters",
        "title": "Urgent German Shepherds & Belgian Malinois on California Shelter Lists | UrgentPaws",
        "h1": "Urgent German Shepherds & Belgian Malinois in California Shelters",
        "meta_desc": "Live tracker of German Shepherd Dogs and Belgian Malinois on California municipal shelter urgent lists, euthanasia alerts, and rescue-only boards.",
        "intro": "German Shepherds, Belgian Malinois, and Shepherd mixes currently at risk in California municipal animal shelters.",
        "filter_fn": lambda d: bool(re.search(r"\b(shepherd|malinois)\b", d.get("breed") or "", re.IGNORECASE)),
    },
    {
        "slug": "urgent-pit-bulls-california",
        "name": "Urgent Pit Bulls & Bully Breeds in California Shelters",
        "title": "Urgent Pit Bulls, Staffies & Bully Breeds on California Shelter Lists | UrgentPaws",
        "h1": "Urgent Pit Bulls, Staffies & Bully Breeds in California Shelters",
        "meta_desc": "Live tracker of American Pit Bull Terriers, Staffordshire Terriers, and bully breed dogs on California shelter euthanasia lists and rescue-only boards.",
        "intro": "Pit Bull Terriers, Staffordshire Terriers, American Bullies, and mixes currently on California municipal shelter urgent and rescue-only feeds.",
        "filter_fn": lambda d: bool(re.search(r"\b(pit\s*bull|staffordshire|bully|bulldog)\b", d.get("breed") or "", re.IGNORECASE)),
    },
]


def clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def is_clean_meta(val: str, max_len: int = 55) -> bool:
    """Rejects promo banners or runaway HTML text captured during profile parsing."""
    s = clean_space(val)
    if not s or len(s) > max_len:
        return False
    return not BOILERPLATE_PATTERN.search(s)


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


def resolve_record_name(
    item: dict | None,
    aid: str,
    dom_text: str = "",
    dom_heading: str = "",
    fallback_prefix: str = "",
) -> str:
    """Extracts a clean dog name from JSON fields, DOM headings, or card text lines."""
    candidates: list[str] = []

    if item:
        for k in (
            "AnimalName", "animalName", "animal_name", "ANIMALNAME",
            "PetName", "petName", "pet_name", "PETNAME",
            "Name", "name", "NAME", "CallName", "callName", "call_name",
            "DogName", "dogName", "Title", "title", "Label", "label",
        ):
            val = clean_space(str(item.get(k) or ""))
            if val:
                candidates.append(val)

    if dom_heading:
        candidates.append(clean_space(dom_heading))

    if dom_text:
        named_field = extract_field(dom_text, ("Name", "Pet Name", "Animal Name", "Meet"))
        if named_field:
            candidates.append(named_field)
        for ln in dom_text.splitlines():
            s = clean_space(ln)
            if s and len(s) <= 35 and not (s.isupper() and " " in s):
                candidates.append(s)

    for raw in candidates:
        cleaned = re.sub(rf"\(?\b{re.escape(aid)}\b\)?", "", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(?:Meet|Name|ID)\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip(" -–|:#•")
        if not cleaned or len(cleaned) < 2 or len(cleaned) > 35:
            continue
        if re.fullmatch(r"A\d{5,8}", cleaned, re.IGNORECASE):
            continue
        if re.search(r"\d+\s*(?:yrs?|years?|mos?|months?|wks?|weeks?|lbs?)\b", cleaned, re.IGNORECASE):
            continue
        if NAME_NOISE_RE.match(cleaned) or not is_clean_meta(cleaned, 35):
            continue
        return cleaned.title() if cleaned.isupper() else cleaned

    return f"{fallback_prefix} {aid}".strip() if fallback_prefix else aid


def load_shelters() -> list[dict]:
    with open("shelters.json", "r", encoding="utf-8") as f:
        return json.load(f)


def is_deadline_expired(dog: dict, shelter_tz: str, now_utc: datetime) -> bool:
    """Checks explicit MM/DD/YY(YY) deadlines against the shelter's local date."""
    try:
        local_today = now_utc.astimezone(ZoneInfo(shelter_tz)).date()
    except Exception:
        local_today = now_utc.astimezone(ZoneInfo("America/Los_Angeles")).date()

    blob = f"{dog.get('deadline_or_urgency') or ''} {dog.get('urgency_type') or ''}"
    for m in DATE_PATTERN.finditer(blob):
        month, day, year_str = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(year_str) + 2000 if len(year_str) == 2 else int(year_str)
        try:
            deadline_date = datetime(year, month, day).date()
            if deadline_date < local_today:
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


def wait_for_petconnect_cards(page, timeout_ms: int = 7000) -> None:
    """Waits for 24PetConnect cards on the main frame or any embedded iframe."""
    try:
        page.wait_for_selector(PETCONNECT_CARD_SELECTOR, timeout=timeout_ms)
        return
    except Exception:
        pass

    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            frame.wait_for_selector(PETCONNECT_CARD_SELECTOR, timeout=3000)
            return
        except Exception:
            continue


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


def fetch_24petconnect_detail_http(dog: dict) -> tuple[dict, str, str, bool]:
    """Fetches a 24PetConnect detail page over HTTP for concurrent inspection."""
    url = dog.get("listing_url") or ""
    if not url:
        return (dog, "", "", False)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw_html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return (dog, "", "", False)

    stripped = re.sub(r"<(script|style|noscript)[^>]*>[\s\S]*?</\1>", " ", raw_html, flags=re.IGNORECASE)
    body_lines = re.sub(r"<(?:br|/?p|/?div|/?li|/?tr|/?h[1-6])[^>]*>", "\n", stripped, flags=re.IGNORECASE)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body_lines))
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body).strip()

    aid = (dog.get("animal_id") or "").upper()
    has_content = len(body) >= 150 and (
        (aid and aid in body.upper())
        or bool(re.search(r"More Info|Breed|Status|DEADLINE|at risk|Needs Placement", body, re.IGNORECASE))
    )
    if not has_content:
        return (dog, "", "", False)

    img_src = ""
    if not dog.get("photo_url"):
        img_match = re.search(
            r'<img[^>]+src=["\']([^"\']*(?:24petconnect|petplace|amazonaws)[^"\']*)["\']',
            raw_html,
            re.IGNORECASE,
        )
        if img_match:
            img_src = html.unescape(img_match.group(1))

    return (dog, body, img_src, True)


def parse_sbct_detail(body: str) -> dict | None:
    """Filters SBCT 24PetConnect detail pages to hard-deadline, rescue-only, or foster-urgent dogs."""
    clean_text = clean_space(body)
    if not clean_text:
        return None

    deadline_match = SBCT_DEADLINE_RE.search(clean_text)
    rescue_match = SBCT_RESCUE_ONLY_RE.search(clean_text)
    foster_match = SBCT_FOSTER_RE.search(clean_text)

    if not (deadline_match or rescue_match or foster_match):
        return None

    if deadline_match:
        deadline_str = deadline_match.group(1)
        badge = f"DEADLINE {deadline_str}"
        urgency_type = "Euthanasia / Exit Deadline"
    elif rescue_match:
        reason = clean_space(rescue_match.group(1) or "").upper()
        badge = f"RESCUE ONLY ({reason})"[:38] if reason else "RESCUE ONLY"
        urgency_type = "Rescue Only (Medical / Behavioral)"
    else:
        badge = "FOSTER / RESCUE NEEDED"
        urgency_type = "Foster / Rescue Needed"

    needs_type = (
        "Rescue Pull Only"
        if rescue_match
        else ("Urgent Foster / Adopt" if foster_match else "Rescue Pull, Foster, or Adopt")
    )

    name_m = re.search(r"\bName:\s*([A-Za-z][A-Za-z0-9' .-]{1,30}?)(?=\s+Species:|\s+Breed:)", clean_text)
    parsed_name = clean_space(name_m.group(1)) if name_m else ""

    wt_m = re.search(r"weigh approximately\s+([\d.]+)\s*lbs", clean_text, re.IGNORECASE)
    parsed_size = f"{wt_m.group(1)} lbs" if wt_m else ""

    note_parts = []
    if deadline_match:
        note_parts.append(f"Needs placement by {deadline_match.group(1)}.")
    if rescue_match:
        note_parts.append("I can " + rescue_match.group(0).rstrip(".") + ".")
    training_match = re.search(r"I may benefit from additional training[^.]*\.", clean_text, re.IGNORECASE)
    if training_match:
        note_parts.append(training_match.group(0))

    out = {
        "deadline_or_urgency": badge,
        "urgency_type": urgency_type,
        "needs_type": needs_type,
        "transport_note": clean_space(" ".join(note_parts))[:160],
    }
    if parsed_name and is_clean_meta(parsed_name, 35):
        out["name"] = parsed_name
    if parsed_size:
        out["size"] = parsed_size
    return out


# ---------------------------------------------------------------------------
# PORTAL 1: STANISLAUS COUNTY - RED STATUS & FINAL CALL
# ---------------------------------------------------------------------------
def scrape_stanislaus_red(page, cfg: dict, now_iso: str) -> list[dict]:
    url = cfg["portal_config"]["url"]
    print(f"Scraping {cfg['name']} ({cfg['slug']})...", flush=True)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_selector("text=/\\bA\\d{5,7}\\b/", timeout=5000)
        except Exception:
            pass
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
            "shelter_slug": cfg["slug"],
            "animal_id": aid,
            "name": name,
            "breed": "",
            "shelter": "Stanislaus Animal Services (Modesto)",
            "city": cfg["city"],
            "county": cfg["county"],
            "state": cfg["state"],
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

    print(f"  {cfg['name']} saved: {len(results)}", flush=True)
    return results


# ---------------------------------------------------------------------------
# PORTAL 2: RESCUEGROUPS V5 API (e.g., OAS Org 5970)
# ---------------------------------------------------------------------------
def fetch_rescuegroups(cfg: dict, now_iso: str) -> list[dict]:
    org_id = str(cfg["portal_config"]["org_id"]).strip()
    print(f"Fetching {cfg['name']} via RescueGroups API (Org {org_id})...", flush=True)
    api_key = os.environ.get("RESCUEGROUPS_API_KEY", "").strip()
    if not api_key:
        print("  RESCUEGROUPS_API_KEY not found in environment; skipping.", flush=True)
        return []

    url = (
        f"https://api.rescuegroups.org/v5/public/orgs/{org_id}/animals/search/available/dogs"
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
        print(f"  RescueGroups API error ({cfg['slug']}): {exc}", flush=True)
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
    prefix = "oas" if cfg["slug"] == "oakland-animal-services" else f"rg-{org_id}"

    for item in payload.get("data", []):
        attrs = item.get("attributes") or {}
        rels = item.get("relationships") or {}

        rg_id = str(item.get("id") or "").strip()
        aid = clean_space(str(attrs.get("rescueId") or rg_id))
        name = clean_space(attrs.get("name") or f"Dog #{aid}")

        priority_raw = str(attrs.get("priority") or "").strip()
        kill_date_raw = str(attrs.get("killDate") or attrs.get("reviewDate") or "").strip()

        deadline_str = ""
        if kill_date_raw and not kill_date_raw.startswith("2500"):
            m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", kill_date_raw)
            if m_iso:
                deadline_str = f"{int(m_iso.group(2))}/{int(m_iso.group(3))}/{m_iso.group(1)}"

        is_urgent = bool(deadline_str) or (priority_raw not in ("", "0", "0.0", "None", "null"))
        if not is_urgent:
            continue

        if deadline_str:
            badge = f"DEADLINE {deadline_str}"
            urgency_type = f"Exit Deadline ({deadline_str})"
        else:
            badge = "PRIORITY URGENT"
            urgency_type = "Priority Urgent Dog"

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
        listing_url = (
            f"https://www.oaklandanimalservices.org/rescue-needed/animal?animalId={listing_id}"
            if cfg["slug"] == "oakland-animal-services"
            else clean_space(attrs.get("url") or f"https://www.rescuegroups.org/animals/detail?AnimalID={listing_id}")
        )

        results.append({
            "id": f"{prefix}-{listing_id}",
            "shelter_slug": cfg["slug"],
            "animal_id": listing_id,
            "name": name,
            "breed": breed,
            "shelter": cfg["name"],
            "city": cfg["city"],
            "county": cfg["county"],
            "state": cfg["state"],
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

    print(f"  {cfg['name']} saved: {len(results)}", flush=True)
    return results


# ---------------------------------------------------------------------------
# PORTAL 3: 24PETCONNECT BOARDS (Supports pagination + concurrent detail checks)
# ---------------------------------------------------------------------------
def scrape_24petconnect_shelter(page, cfg: dict, now_iso: str) -> list[dict]:
    boards = cfg.get("portal_config", {}).get("boards", [])
    shelter_results = []

    for board in boards:
        base_board_url = board["url"]
        shelter_name = board.get("shelter_name") or cfg["name"]
        portal_code = board["portal_code"]
        require_urgent_flag = board.get("require_urgent_flag", False)
        inspect_details = board.get("inspect_details_for_deadline", False)
        max_pages = int(board.get("max_pages") or 1)
        max_checks = int(board.get("max_detail_checks") or 35)
        default_badge = board.get("default_badge", "Urgent / Rescue Needed")
        default_urgency = board.get("default_urgency", "At-Risk Shelter Dog")

        raw_cards = []
        seen_card_keys = set()

        for page_idx in range(max_pages):
            offset = page_idx * 30
            if offset == 0:
                page_url = base_board_url
            else:
                sep = "&" if "?" in base_board_url else "?"
                page_url = f"{base_board_url}{sep}index={offset}"

            print(f"Scraping {shelter_name} ({page_url})...", flush=True)
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
                wait_for_petconnect_cards(page, timeout_ms=7000)
            except Exception as exc:
                print(f"  Load error ({shelter_name} page {page_idx + 1}): {exc}", flush=True)
                break

            page_cards = find_petconnect_cards_across_frames(page)
            new_on_page = 0
            for c in page_cards:
                k = (c.get("href") or "") + "|" + (c.get("text") or "")[:80]
                if k not in seen_card_keys:
                    seen_card_keys.add(k)
                    raw_cards.append(c)
                    new_on_page += 1

            if new_on_page < 25:
                break

        print(f"  Detected {len(raw_cards)} raw cards on {shelter_name}", flush=True)

        board_results = []
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
            if not name or name.upper() == animal_id:
                name = f"Dog {animal_id}"

            breed = extract_field(txt, ("Breed",))
            age = extract_field(txt, ("Age",))
            reason = extract_field(txt, ("Reason for Rescue", "Substatus"))
            status = extract_field(txt, ("Status",))
            location = extract_field(txt, ("Located at", "Location")) or shelter_name
            photo_url = c.get("img") or ""

            if "CCASDTransferPreferredPets" in base_board_url:
                href = f"https://24petconnect.com/CCASDTransferPreferredPets/Details/CCST/{animal_id}"
            elif "CCASDAvailablePets" in base_board_url:
                href = f"https://24petconnect.com/CCASDAvailablePets/Details/CCST/{animal_id}"
            elif portal_code == "SBCT":
                href = f"https://24petconnect.com/DetailsMain/SBCT/{animal_id}"
            elif portal_code == "KernCountyRescue" or "KernCountyRescue/Details" in href or not href:
                code_for_link = "KERN" if portal_code == "KernCountyRescue" else portal_code
                href = f"https://24petconnect.com/DetailsMain/{code_for_link}/{animal_id}"

            urgency_type = default_urgency
            badge = default_badge
            note = reason
            is_rescue_only = bool(RESCUE_ONLY_PATTERN.search(status + " " + txt + " " + default_badge))
            needs = "Rescue Pull Only" if is_rescue_only else "Rescue Pull or Adopt"

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

            board_results.append({
                "id": f"24pc-{portal_code.lower()}-{animal_id.lower()}",
                "shelter_slug": cfg["slug"],
                "animal_id": animal_id,
                "name": name,
                "breed": breed,
                "shelter": location,
                "city": cfg["city"],
                "county": cfg["county"],
                "state": cfg["state"],
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

        if inspect_details and board_results:
            candidates = board_results[:max_checks]
            with ThreadPoolExecutor(max_workers=14) as pool:
                http_results = list(pool.map(fetch_24petconnect_detail_http, candidates))

            verified = []
            for dog, body, img_src, ok in http_results:
                if not ok:
                    try:
                        page.goto(dog["listing_url"], wait_until="domcontentloaded", timeout=10000)
                        try:
                            page.wait_for_selector("text=/More Info|DEADLINE|Needs Placement|Status|Breed/i", timeout=3000)
                        except Exception:
                            pass
                        body = page.inner_text("body")
                        if not dog["photo_url"]:
                            img_src = page.eval_on_selector(
                                "img[src*='24petconnect'], img[src*='petplace'], img[src*='amazonaws']",
                                "el => el ? el.src : ''",
                            )
                    except Exception:
                        continue

                if RESCUED_PATTERN.search(body) or NON_DOG_PATTERN.search(body[:600]):
                    continue

                if portal_code == "SBCT":
                    sbct_info = parse_sbct_detail(body)
                    if not sbct_info:
                        continue
                    dog.update(sbct_info)
                    if not dog["photo_url"] and img_src:
                        dog["photo_url"] = img_src
                    verified.append(dog)
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

                if not dog["photo_url"] and img_src:
                    dog["photo_url"] = img_src

                verified.append(dog)
            board_results = verified

        print(f"  {shelter_name} urgent dogs saved: {len(board_results)}", flush=True)
        shelter_results.extend(board_results)

    return shelter_results


# ---------------------------------------------------------------------------
# PORTAL 4: LA ANIMAL SERVICES (6 CITY SHELTERS) - NEW HOPE ALERT BOARDS
# ---------------------------------------------------------------------------
def enrich_laas_from_petharbor(dog: dict) -> bool:
    global LAAS_WEB_BLOCKED
    aid = (dog.get("animal_id") or "").strip()
    if not aid:
        return False

    before_name = dog.get("name")
    laas_pet_url = f"https://www.laanimalservices.com/pet/{aid.lower()}"
    petharbor_url = dog.get("listing_url") or ""

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
                    if is_clean_meta(parsed_name, 35) and parsed_name.upper() != "LAAS":
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

                m_size = re.search(r"\bSize\s+([^\n]+?)(?=\s+Color\b|\s+Breed\b|\s+Lifestage\b)", plain, re.IGNORECASE)
                if m_size and not dog.get("size") and is_clean_meta(m_size.group(1), 25):
                    dog["size"] = clean_space(m_size.group(1))

                m_breed = re.search(r"\bBreed\s+([^\n]+?)(?=\s+Lifestage\b|\s+Care Status\b|\s+Medical\b)", plain, re.IGNORECASE)
                if m_breed and not dog.get("breed") and is_clean_meta(m_breed.group(1), 45):
                    dog["breed"] = clean_space(m_breed.group(1))

                m_stage = re.search(r"\bLifestage\s+([^\n]+?)(?=\s+Care Status\b|\s+Medical\b|\s+Sex\b)", plain, re.IGNORECASE)
                if m_stage and not dog.get("age") and is_clean_meta(m_stage.group(1), 25):
                    dog["age"] = clean_space(m_stage.group(1))

                return dog.get("name") != before_name
        except Exception:
            LAAS_WEB_BLOCKED = True

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
    if is_clean_meta(parsed_name, 35) and parsed_name.upper() != "LAAS":
        dog["name"] = parsed_name.title() if parsed_name.isupper() else parsed_name

    m_branch = re.search(r"Los Angeles Animal Services\s*-\s*([A-Za-z ]+?Shelter)\b", ph_plain, re.IGNORECASE)
    if m_branch:
        branch = clean_space(m_branch.group(1)).replace(" Shelter", "").strip()
        if branch and is_clean_meta(branch, 30):
            dog["shelter"] = f"LA Animal Services ({branch})"

    if not dog.get("age"):
        m_age = re.search(r"\babout\s+(\d+\s+(?:years?|months?|weeks?)\s+old)\b", ph_plain, re.IGNORECASE)
        if m_age and is_clean_meta(m_age.group(1), 25):
            dog["age"] = clean_space(m_age.group(1))

    if not dog.get("size"):
        m_wt = re.search(r"\bapproximately\s+(\d+)\s+pounds?\b", ph_plain, re.IGNORECASE)
        if m_wt:
            dog["size"] = f"{m_wt.group(1)} lbs"

    if not dog.get("breed"):
        m_breed = re.search(r"\bMy name is\s+[^.]+?\s+and I am (?:a|an)\s+([^.]+)\.", ph_plain, re.IGNORECASE)
        if m_breed and is_clean_meta(m_breed.group(1), 45):
            dog["breed"] = clean_space(m_breed.group(1))

    if RESCUE_ONLY_PATTERN.search(ph_plain):
        dog["needs_type"] = "New Hope Rescue Pull Only"

    return dog.get("name") != before_name


def scrape_laas_newhope_alerts(page, cfg: dict, now_iso: str) -> list[dict]:
    print(f"Scraping {cfg['name']} New Hope Alert boards...", flush=True)
    results = []
    seen_ids = set()

    for board in LAAS_ALERT_BOARDS:
        try:
            page.goto(board["url"], wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_selector(
                    "a[href*='detail.asp'], a[href*='Details'], .gridResult",
                    timeout=5000,
                )
            except Exception:
                pass
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
                if is_clean_meta(name_clean, 35) and name_clean.upper() != "LAAS":
                    name = name_clean
                for cell in cells[1:]:
                    if any(s in cell.lower() for s in ("east valley", "west valley", "west la", "south la", "harbor", "north central", "chesterfield", "shelter")):
                        shelter_loc = f"LA Animal Services ({cell})"
                    elif re.search(r"\b\d+\s*(yr|year|mo|month|wk|week)", cell, re.IGNORECASE) and not age:
                        age = cell
                    elif any(b in cell.lower() for b in ("pit", "terrier", "shepherd", "husky", "mix", "retriever", "chihuahua", "hound", "rottweiler", "bulldog", "boxer", "poodle", "malinois", "doberman")) and not breed:
                        breed = cell

            if not name:
                extracted = extract_field(txt, ("Meet", "Name"))
                name = extracted if is_clean_meta(extracted, 35) else f"LAAS {animal_id}"

            results.append({
                "id": f"laas-{animal_id.lower()}",
                "shelter_slug": cfg["slug"],
                "animal_id": animal_id,
                "name": name,
                "breed": breed,
                "shelter": shelter_loc,
                "city": cfg["city"],
                "county": cfg["county"],
                "state": cfg["state"],
                "size": "",
                "age": age,
                "urgency_type": board["urgency_type"],
                "deadline_or_urgency": board["badge"],
                "needs_type": "New Hope Rescue Pull Only" if RESCUE_ONLY_PATTERN.search(txt) else "New Hope Rescue Pull or Adopt",
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

    print(f"  {cfg['name']} saved: {len(results)} (resolved {enriched_count} names from official profiles)", flush=True)
    return results


# ---------------------------------------------------------------------------
# PORTAL 5: SAN JOSE ACS - ARCGIS REST JSON API
# ---------------------------------------------------------------------------
def fetch_sanjose_arcgis(cfg: dict, now_iso: str) -> list[dict]:
    print(f"Querying {cfg['name']} ArcGIS MapServer Layer 1...", flush=True)
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
            "shelter_slug": cfg["slug"],
            "animal_id": aid,
            "name": name,
            "breed": breed,
            "shelter": cfg["name"],
            "city": cfg["city"],
            "county": cfg["county"],
            "state": cfg["state"],
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

    print(f"  {cfg['name']} saved: {len(results)}", flush=True)
    return results


# ---------------------------------------------------------------------------
# PORTAL 6: OC ANIMAL CARE - PUBLIC RESCUE TRACK PORTAL
# ---------------------------------------------------------------------------
def _extract_dict_records(obj, out: list[dict]) -> None:
    """Recursively collects dict items from nested JSON payloads."""
    if isinstance(obj, list):
        for item in obj:
            _extract_dict_records(item, out)
    elif isinstance(obj, dict):
        if any(k in obj for k in ("AnimalID", "animalId", "animal_id", "PetID", "id", "Id", "AnimalId")):
            out.append(obj)
        for val in obj.values():
            if isinstance(val, (list, dict)):
                _extract_dict_records(val, out)


def scrape_oc_rescuetrack(page, cfg: dict, now_iso: str) -> list[dict]:
    root_url = "https://petadoption.oc.gov/rescuetrackportal/"
    speciallist_url = "https://petadoption.oc.gov/rescuetrackportal/#/speciallist/DOG"
    print(f"Scraping {cfg['name']} Rescue Track portal...", flush=True)

    captured_records: list[dict] = []

    def handle_response(resp):
        try:
            if resp.status != 200 or "petadoption.oc.gov" not in resp.url.lower():
                return
            r_low = resp.url.lower()
            if r_low.endswith((".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2")):
                return
            raw = resp.text()
            if not raw or len(raw) < 30:
                return
            parsed = json.loads(raw)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            _extract_dict_records(parsed, captured_records)
        except Exception:
            pass

    page.on("response", handle_response)
    try:
        page.goto(root_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(1500)
        page.goto(speciallist_url, wait_until="domcontentloaded", timeout=25000)

        try:
            page.wait_for_selector("text=/\\bA\\d{5,8}\\b/", timeout=8500)
        except Exception:
            page.wait_for_timeout(3000)

        if not captured_records:
            scope_items = page.evaluate(
                """() => {
                    const out = [];
                    if (!window.angular) return out;
                    for (const el of Array.from(document.querySelectorAll("*"))) {
                        try {
                            const sc = window.angular.element(el).scope();
                            if (!sc) continue;
                            for (const k of Object.keys(sc)) {
                                if (k.startsWith("$")) continue;
                                const v = sc[k];
                                if (Array.isArray(v) && v.length > 0 && typeof v[0] === "object") {
                                    out.push(...v);
                                }
                            }
                        } catch (e) {}
                    }
                    return out;
                }"""
            )
            if isinstance(scope_items, list):
                _extract_dict_records(scope_items, captured_records)
    except Exception as exc:
        print(f"  OC Rescue Track load error: {exc}", flush=True)
    finally:
        try:
            page.remove_listener("response", handle_response)
        except Exception:
            pass

    results: list[dict] = []
    seen_ids: set[str] = set()

    for item in captured_records:
        species = str(item.get("Species") or item.get("AnimalType") or item.get("animalType") or "Dog").lower()
        if species and "dog" not in species and "canine" not in species:
            continue
        aid = str(
            item.get("AnimalID") or item.get("AnimalId") or item.get("animalId")
            or item.get("animal_id") or item.get("PetID") or item.get("id") or ""
        ).strip().upper()
        if not re.match(r"^A\d{5,8}$", aid) or aid in seen_ids:
            continue
        seen_ids.add(aid)

        name = resolve_record_name(item, aid, fallback_prefix="OC Animal Care")
        breed = clean_space(str(item.get("PrimaryBreed") or item.get("Breed") or item.get("breed") or ""))
        if NON_DOG_PATTERN.search(breed):
            continue
        age = clean_space(str(item.get("Age") or item.get("age") or ""))
        size_clean = clean_space(str(item.get("Weight") or item.get("Size") or item.get("weight") or ""))
        if size_clean and re.match(r"^\d+(?:\.\d+)?$", size_clean):
            size_clean = f"{int(round(float(size_clean)))} lbs"

        code = str(
            item.get("SubStatus") or item.get("RescueStatus") or item.get("StatusCode")
            or item.get("Status") or item.get("RescueCode") or item.get("SpecialCode")
            or item.get("Category") or "RESQ"
        )
        code_upper = clean_space(code).upper()
        if "BHV" in code_upper or "BEHAV" in code_upper:
            badge = "OC RESQ-BHV"
            urgency_type = "OC Rescue Track — Behavioral (RESQ-BHV)"
        elif "MED" in code_upper:
            badge = "OC MEDICAL"
            urgency_type = "OC Rescue Track — Medical"
        elif code_upper and code_upper != "RESQ":
            badge = f"OC {code_upper}"[:28]
            urgency_type = f"OC Rescue Track ({code_upper})"
        else:
            badge = "OC RESQ"
            urgency_type = "OC Rescue Track Priority"

        note_clean = clean_space(str(item.get("Memo") or item.get("Notes") or item.get("Reason") or item.get("Description") or ""))
        contact_hint = "Rescue desk: rescue@occr.ocgov.com"
        full_note = f"{note_clean} • {contact_hint}".strip(" •") if note_clean else contact_hint

        results.append({
            "id": f"ocac-{aid.lower()}",
            "shelter_slug": cfg["slug"],
            "animal_id": aid,
            "name": name,
            "breed": breed,
            "shelter": cfg["name"],
            "city": cfg["city"],
            "county": cfg["county"],
            "state": cfg["state"],
            "size": size_clean,
            "age": age,
            "urgency_type": urgency_type,
            "deadline_or_urgency": badge,
            "needs_type": "Rescue Pull Only",
            "transport_note": full_note[:180],
            "photo_url": f"https://petadoption.oc.gov/rescuetrackportal/thumbs/servicethumb2.php?tab=special&detailid={aid}",
            "listing_url": f"https://petadoption.oc.gov/rescuetrackportal/#/specialdetail/detail/{aid}",
            "updated_at": now_iso,
            "last_checked": now_iso,
        })

    print(f"  {cfg['name']} saved: {len(results)}", flush=True)
    return results


# ---------------------------------------------------------------------------
# PORTAL 7: LA COUNTY DACC (7 CARE CENTERS) - RESCUE ONLY & AP-KHOA
# ---------------------------------------------------------------------------
def format_dacc_care_center(raw_loc: str) -> tuple[str, str]:
    loc_clean = clean_space(raw_loc)
    loc_lower = loc_clean.lower()
    for key, email in DACC_CENTER_EMAILS.items():
        if key in loc_lower:
            center_label = "Carson / Gardena" if key in ("carson", "gardena") else key.title()
            return f"LA County DACC ({center_label})", email
    return (f"LA County DACC ({loc_clean})" if loc_clean else "LA County DACC"), ""


def scrape_lacounty_dacc(page, cfg: dict, now_iso: str) -> list[dict]:
    print(f"Scraping {cfg['name']} (Rescue Only & AP-KHOA)...", flush=True)
    results: list[dict] = []
    seen_ids: set[str] = set()

    bad_tokens = (
        "logo", "icon", "placeholder", "noimage", "no_image", "no-image",
        "nophoto", "no_photo", "no-photo", "default", "spinner", "heart",
        "coming-soon", "coming_soon", "silhouette", "generic", "avatar",
        "040-pets", "goback", "banner", "header", "footer", "wp-content/themes",
    )

    def resolve_dacc_photo(item: dict | None, dom_imgs: list[str] | None = None) -> str:
        # DACC JSON explicitly reports imageCount; 0 means only the 040-pets placeholder exists
        if item is not None:
            try:
                if int(item.get("imageCount") or 0) <= 0:
                    return ""
            except (TypeError, ValueError):
                pass

        candidates: list[str] = []
        if item:
            for k in (
                "PhotoUrl", "photoUrl", "PhotoURL", "photo_url", "ImageUrl", "imageUrl",
                "image_url", "Photo", "photo", "Image", "image", "Picture", "picture",
                "Thumbnail", "thumbnail", "Pic", "pic", "PhotoLink", "FileName",
            ):
                val = clean_space(str(item.get(k) or ""))
                if val:
                    candidates.append(val)
            for val in item.values():
                if isinstance(val, str) and re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", val.strip(), re.IGNORECASE):
                    candidates.append(val.strip())
        if dom_imgs:
            candidates.extend(clean_space(u) for u in dom_imgs if u)

        for raw in candidates:
            low = raw.lower()
            if not raw or low.startswith("data:") or any(bad in low for bad in bad_tokens):
                continue
            if raw.startswith("//"):
                return f"https:{raw}"
            if raw.startswith("http"):
                return raw
            return f"https://animalcare.lacounty.gov/{raw.lstrip('/')}"
        return ""

    # Start on homepage to clear Incapsula JS challenge, then follow "View Our Animals"
    animal_search_url = ""
    try:
        page.goto("https://animalcare.lacounty.gov/", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)
        animal_search_url = page.evaluate(
            """() => {
                const a = Array.from(document.querySelectorAll("a")).find(el =>
                    /view our animals/i.test(el.innerText || "")
                );
                return a ? a.href : "";
            }"""
        )
    except Exception as exc:
        print(f"  LA County DACC homepage error: {exc}", flush=True)

    target_search_url = animal_search_url or "https://animalcare.lacounty.gov/dacc-search/"

    status_targets = [
        ("rescue", "DACC RESCUE ONLY", "LA County DACC — Rescue Only", "Rescue Pull Only"),
        ("khoa", "DACC AP-KHOA", "LA County DACC — AP-KHOA (Adoption Partner Only)", "Rescue Pull Only"),
    ]

    for keyword, badge, urgency_type, needs in status_targets:
        captured_json: list[dict] = []
        accept_responses = False

        def handle_dacc_resp(resp):
            if not accept_responses:
                return
            try:
                if resp.status != 200 or "lacounty.gov" not in resp.url.lower():
                    return
                rtype = resp.request.resource_type
                ctype = (resp.headers.get("content-type") or "").lower()
                if rtype not in ("xhr", "fetch") and "json" not in ctype:
                    return
                raw = resp.text()
                if not raw or len(raw) < 30:
                    return
                parsed = json.loads(raw)
                _extract_dict_records(parsed, captured_json)
            except Exception:
                pass

        page.on("response", handle_dacc_resp)
        try:
            page.goto(target_search_url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(1500)

            # Only capture XHRs triggered by our filtered search submission (ignore initial unfiltered load)
            accept_responses = True
            for frame in page.frames:
                try:
                    frame.evaluate(
                        """(statusKeyword) => {
                            const selects = Array.from(document.querySelectorAll("select"));
                            if (!selects.length) return;
                            let matchedStatus = false;
                            for (const sel of selects) {
                                const opts = Array.from(sel.options);
                                const dogOpt = opts.find(o => /^dog$/i.test((o.text || "").trim()) || /^dog$/i.test((o.value || "").trim()));
                                if (dogOpt) {
                                    sel.value = dogOpt.value;
                                    sel.dispatchEvent(new Event("change", { bubbles: true }));
                                }
                                const stOpt = opts.find(o =>
                                    (o.text || "").toLowerCase().includes(statusKeyword) ||
                                    (o.value || "").toLowerCase().includes(statusKeyword)
                                );
                                if (stOpt) {
                                    sel.value = stOpt.value;
                                    sel.dispatchEvent(new Event("change", { bubbles: true }));
                                    matchedStatus = true;
                                }
                            }
                            const submitBtn = Array.from(document.querySelectorAll("button, input[type='submit'], a.btn, a.button")).find(b =>
                                /search|find|submit|filter/i.test((b.innerText || b.value || ""))
                            );
                            if (matchedStatus && submitBtn) {
                                submitBtn.click();
                            }
                        }""",
                        keyword,
                    )
                except Exception:
                    continue

            page.wait_for_timeout(3500)
        except Exception as exc:
            print(f"  LA County DACC ({keyword}) error: {exc}", flush=True)
        finally:
            try:
                page.remove_listener("response", handle_dacc_resp)
            except Exception:
                pass

        dom_cards_all: list[dict] = []
        for frame in page.frames:
            try:
                frame_cards = frame.evaluate(
                    """() => {
                        const out = [];
                        const idRe = /\\bA\\d{6,8}\\b/i;
                        const seen = new Set();
                        for (const el of Array.from(document.querySelectorAll(".card, [class*='animal'], [class*='pet'], [class*='result'], [class*='grid'], article, tr, li"))) {
                            const txt = (el.innerText || "").trim();
                            if (!txt || txt.length < 12 || txt.length > 1800) continue;
                            const m = txt.match(idRe);
                            if (!m) continue;
                            const nested = Array.from(el.children).some(ch => idRe.test(ch.innerText || "") && ch.querySelector("img"));
                            if (nested) continue;
                            const aid = m[0].toUpperCase();
                            if (seen.has(aid)) continue;
                            seen.add(aid);

                            const imgs = [];
                            for (const img of Array.from(el.querySelectorAll("img"))) {
                                const srcset = img.getAttribute("data-srcset") || img.getAttribute("srcset") || "";
                                const firstSet = srcset ? srcset.split(",")[0].trim().split(" ")[0] : "";
                                for (const u of [
                                    img.getAttribute("data-src"),
                                    img.getAttribute("data-lazy-src"),
                                    img.getAttribute("data-original"),
                                    img.getAttribute("ng-src"),
                                    firstSet,
                                    img.getAttribute("src"),
                                    img.currentSrc
                                ]) {
                                    if (u) imgs.push(u);
                                }
                            }
                            const heading = el.querySelector("h1, h2, h3, h4, .name, [class*='name'], [class*='title'], strong, b");
                            const headingTxt = heading ? (heading.innerText || "").trim() : "";
                            const a = el.querySelector("a[href]");
                            out.push({
                                aid,
                                text: txt,
                                heading: headingTxt,
                                imgs,
                                href: a ? a.href : ""
                            });
                        }
                        return out;
                    }"""
                )
                dom_cards_all.extend(frame_cards or [])
            except Exception:
                continue

        dom_by_id = {c["aid"]: c for c in dom_cards_all if c.get("aid")}

        for item in captured_json:
            aid = str(
                item.get("animalId") or item.get("animalID") or item.get("AnimalID")
                or item.get("animal_id") or item.get("PetID") or item.get("id") or ""
            ).strip().upper()
            if not re.match(r"^A\d{6,8}$", aid) or aid in seen_ids:
                continue

            atype = str(item.get("animalType") or item.get("AnimalType") or item.get("species") or "").strip().upper()
            if atype != "DOG":
                continue

            breed = clean_space(str(item.get("breed") or item.get("primaryBreed") or ""))
            if NON_DOG_PATTERN.search(breed):
                continue

            # Verify record actually belongs to the requested Rescue Only / AP-KHOA status
            status_blob = " ".join(
                str(item.get(k) or "")
                for k in ("kennelStat", "kennelSubStat", "rescueOnly", "medicalStat", "outcomeType")
            ).upper()
            if keyword == "rescue" and "RESCUE" not in status_blob:
                continue
            if keyword == "khoa" and "KHOA" not in status_blob:
                continue

            seen_ids.add(aid)
            dom_match = dom_by_id.get(aid) or {}

            raw_json_name = clean_space(str(item.get("animalName") or ""))
            if not raw_json_name or raw_json_name.upper() == aid:
                name = f"DACC {aid}"
            else:
                name = resolve_record_name(item, aid, fallback_prefix="DACC")

            yrs = int(item.get("yearsOld") or 0)
            mos = int(item.get("monthsOld") or 0)
            age_parts = [f"{yrs} yrs" if yrs else "", f"{mos} mos" if mos else ""]
            age = " ".join(p for p in age_parts if p) or clean_space(str(item.get("animalAge") or ""))

            wt = item.get("weight")
            size = f"{int(round(float(wt)))} lbs" if wt else clean_space(str(item.get("animalSize") or ""))

            center = str(item.get("location") or item.get("animalCareCenter") or "")
            shelter_label, center_email = format_dacc_care_center(center)
            photo = resolve_dacc_photo(item, dom_match.get("imgs") or [])
            detail_url = clean_space(str(item.get("detailUrl") or dom_match.get("href") or "")) or (
                f"{target_search_url.split('?')[0]}?animalCareCenter=ALL&animalType=DOG&sex=ALL&breed=ALL&animalAge=ALL&animalSize=ALL&animalStatus=ALL&animalID={aid}"
            )

            results.append({
                "id": f"dacc-{aid.lower()}",
                "shelter_slug": cfg["slug"],
                "animal_id": aid,
                "name": name,
                "breed": breed,
                "shelter": shelter_label,
                "city": cfg["city"],
                "county": cfg["county"],
                "state": cfg["state"],
                "size": size,
                "age": age,
                "urgency_type": urgency_type,
                "deadline_or_urgency": badge,
                "needs_type": needs,
                "transport_note": f"Rescue desk: {center_email}" if center_email else f"Status: {badge}",
                "photo_url": photo,
                "listing_url": detail_url,
                "updated_at": now_iso,
                "last_checked": now_iso,
                "_image_count": int(item.get("imageCount") or 0),
            })

    # Only check detail pages for dogs where DACC reports imageCount > 0
    missing_with_images = [d["animal_id"] for d in results if not d.get("photo_url") and d.get("_image_count", 0) > 0]
    if missing_with_images:
        base_search = target_search_url.split("?")[0]
        detail_photo_candidates = page.evaluate(
            """async ({ baseSearch, aids }) => {
                const out = {};
                await Promise.all(aids.map(async (aid) => {
                    try {
                        const u = `${baseSearch}?animalCareCenter=ALL&animalType=DOG&sex=ALL&breed=ALL&animalAge=ALL&animalSize=ALL&animalStatus=ALL&animalID=${aid}`;
                        const resp = await fetch(u, { credentials: "include" });
                        const html = await resp.text();
                        const doc = new DOMParser().parseFromString(html, "text/html");
                        const imgs = Array.from(doc.querySelectorAll("img"))
                            .map(i => i.getAttribute("data-src") || i.getAttribute("src") || "")
                            .filter(Boolean);
                        out[aid] = imgs;
                    } catch (e) {}
                }));
                return out;
            }""",
            {"baseSearch": base_search, "aids": missing_with_images},
        )
        if isinstance(detail_photo_candidates, dict):
            for d in results:
                if not d.get("photo_url"):
                    cand = resolve_dacc_photo(None, detail_photo_candidates.get(d["animal_id"]) or [])
                    if cand:
                        d["photo_url"] = cand

    for d in results:
        d.pop("_image_count", None)

    with_photos = sum(1 for d in results if d.get("photo_url"))
    print(f"  {cfg['name']} saved: {len(results)} (with real photos: {with_photos})", flush=True)
    return results


# ---------------------------------------------------------------------------
# NORMALIZATION & STATIC SEO GENERATION
# ---------------------------------------------------------------------------
def infer_shelter_slug(dog: dict, shelters_by_county: dict[str, str]) -> str:
    if dog.get("shelter_slug"):
        return dog["shelter_slug"]
    county = (dog.get("county") or "").lower()
    return shelters_by_county.get(county, "")


def normalize_dog_record(dog: dict, shelter_cfg: dict | None) -> dict:
    """Standardizes needs_type into exact Rescue-Only, Foster-Eligible, or Adoptable categories."""
    for field, limit in (("breed", 50), ("age", 25), ("size", 25)):
        val = dog.get(field) or ""
        if val and not is_clean_meta(val, limit):
            dog[field] = ""

    if shelter_cfg and not dog.get("shelter_slug"):
        dog["shelter_slug"] = shelter_cfg["slug"]

    slug = (dog.get("shelter_slug") or "").lower()
    is_laas = slug == "la-animal-services"
    foster_capable = bool(shelter_cfg.get("foster_capable")) if shelter_cfg else False

    combined = " ".join(
        str(dog.get(k) or "")
        for k in ("needs_type", "urgency_type", "deadline_or_urgency", "transport_note")
    )

    if RESCUE_ONLY_PATTERN.search(combined):
        dog["needs_type"] = "New Hope Rescue Pull Only" if is_laas else "Rescue Pull Only"
    elif "foster priority" in (dog.get("urgency_type") or "").lower():
        dog["needs_type"] = "Urgent Foster / Adopt"
    elif foster_capable or re.search(r"\bfoster\b", combined, re.IGNORECASE):
        dog["needs_type"] = "Rescue Pull, Foster, or Adopt"
    elif is_laas:
        dog["needs_type"] = "New Hope Rescue Pull or Adopt"
    else:
        dog["needs_type"] = "Rescue Pull or Adopt"

    return dog


def sync_static_page_navs(shelters: list[dict] | None = None) -> None:
    """Enforces nav labels, active page underline, and unified compact footer across static pages."""
    info_pages = {
        "about-urgent-paws.html": "about-urgent-paws",
        "for-shelters.html": "for-shelters",
        "blog.html": "blog",
        "contact-us.html": "contact-us",
    }

    unified_footer = ""
    if shelters:
        shelter_links_html = "\n              ".join(
            f'<a href="{html.escape(cfg["slug"])}" class="hover:text-neutral-950 underline truncate">{html.escape(cfg["name"])}</a>'
            for cfg in shelters
        )
        intent_links_html = "\n              ".join(
            f'<a href="{html.escape(lander["slug"])}" class="hover:text-neutral-950 underline">{html.escape(lander["name"])}</a>'
            for lander in PROGRAMMATIC_SEO_LANDERS
        )
        unified_footer = f"""  <footer class="bg-white border-t border-neutral-200 mt-10">
    <div class="w-full px-4 sm:px-6 lg:px-8 py-4 space-y-3 text-xs text-neutral-500">
      <details class="group border-b border-neutral-200 pb-3">
        <summary class="flex items-center justify-between cursor-pointer list-none font-semibold text-neutral-700 hover:text-neutral-950 select-none">
          <span>Browse Urgent Dogs by Shelter &amp; Category</span>
          <svg class="w-4 h-4 text-neutral-500 transition-transform group-open:rotate-180" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
          </svg>
        </summary>

        <nav aria-label="Shelter Urgent Lists" class="pt-3 space-y-3">
          <div>
            <p class="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 mb-1.5">By Shelter</p>
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-x-4 gap-y-1.5 text-neutral-600">
              {shelter_links_html}
            </div>
          </div>
          <div class="pt-2 border-t border-neutral-100">
            <p class="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 mb-1.5">By Urgency &amp; Breed</p>
            <div class="flex flex-wrap gap-x-4 gap-y-1.5 text-neutral-600">
              {intent_links_html}
            </div>
          </div>
        </nav>
      </details>

      <div class="flex flex-wrap items-center justify-between gap-3">
        <span>&copy; 2026 UrgentPaws | Urgent Municipal Shelter Dog Directory</span>
        <div class="flex flex-wrap gap-4">
          <a href="./" class="hover:text-neutral-900">Home</a>
          <a href="about-urgent-paws" class="hover:text-neutral-900">About</a>
          <a href="for-shelters" class="hover:text-neutral-900">For Shelters</a>
          <a href="blog" class="hover:text-neutral-900">Guide</a>
          <a href="contact-us" class="hover:text-neutral-900">Contact Us</a>
        </div>
      </div>
    </div>
  </footer>"""

    for filename, active_slug in info_pages.items():
        if not os.path.exists(filename):
            continue
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()

        updated = re.sub(r">\s*Live Feed\s*<", ">Urgent Dogs<", content)
        updated = re.sub(r">\s*Blog\s*<", ">Guide<", updated)

        def fix_header_nav(match: re.Match) -> str:
            nav_html = match.group(0)
            nav_html = re.sub(
                r"\b(?:underline|underline-offset-4|text-neutral-950)\s*",
                "",
                nav_html,
            )
            nav_html = re.sub(
                rf'(<a\s+href="{re.escape(active_slug)}"\s+class=")',
                r"\1underline underline-offset-4 text-neutral-950 ",
                nav_html,
            )
            return nav_html

        updated = re.sub(
            r'<nav[^>]*aria-label="(?:Primary|Mobile) navigation"[\s\S]*?</nav>',
            fix_header_nav,
            updated,
        )

        if unified_footer:
            updated = re.sub(
                r"<footer[\s\S]*?</footer>",
                unified_footer,
                updated,
                count=1,
            )

        if updated != content:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(updated)

def build_seo_faqs(page_name: str, contact: str, address: str, foster_capable: bool) -> list[tuple[str, str]]:
    """Generates shelter- and intent-specific Q&As for visible content and FAQPage JSON-LD."""
    foster_answer = (
        f"Yes. {page_name} lists foster-eligible at-risk dogs alongside rescue-only cases. "
        "Include the dog's Animal ID (e.g., A123456) when contacting the foster or rescue desk so staff can check availability before a deadline."
        if foster_capable
        else f"Many urgent dogs on {page_name}'s board are restricted to approved 501(c)(3) rescue partners (Rescue Pull Only). "
        "Individuals who want to foster can apply to foster through a local 501(c)(3) rescue partner and share the dog's Animal ID so the rescue can place a pull hold."
    )
    return [
        (
            f"How do 501(c)(3) rescues pull an urgent dog from {page_name}?",
            f"Email the shelter's rescue coordinator ({contact or 'listed on the official shelter card'}) with your 501(c)(3) rescue organization name, puller contact details, and the dog's exact Animal ID (A#). Always verify pickup windows before sending transport.",
        ),
        (
            f"Can the public adopt or foster urgent dogs from {page_name}?",
            foster_answer,
        ),
        (
            f"How often is this {page_name} urgent dog tracker updated?",
            f"UrgentPaws syncs directly with {page_name}'s public feed every 3 hours and automatically removes dogs whose deadlines have passed or that have been marked adopted or rescued. Shelter location: {address or 'California'}.",
        ),
    ]


def render_directory_html(
    *,
    page_url: str,
    title: str,
    meta_desc: str,
    h1: str,
    breadcrumb_label: str,
    intro: str,
    address: str,
    contact: str,
    foster_capable: bool,
    dogs: list[dict],
    footer_shelter_links: str,
    footer_intent_links: str,
    checked_label: str,
    now_iso: str,
) -> str:
    cards_html = []
    ld_items = []

    for idx, dog in enumerate(dogs, start=1):
        d_name = html.escape(dog.get("name") or f"Dog {dog.get('animal_id', '')}")
        d_id = html.escape(dog.get("animal_id") or "")
        d_breed = html.escape(dog.get("breed") or "Mixed Breed")
        d_age = html.escape(dog.get("age") or "")
        d_size = html.escape(dog.get("size") or "")
        d_badge = html.escape(dog.get("deadline_or_urgency") or "Urgent")
        d_needs = html.escape(dog.get("needs_type") or "")
        d_shelter = html.escape(dog.get("shelter") or breadcrumb_label)
        d_note = html.escape(dog.get("transport_note") or "")
        d_url = html.escape(dog.get("listing_url") or "https://www.urgentpaws.org")
        d_photo = html.escape(dog.get("photo_url") or "")

        meta_bits = [bit for bit in [d_breed, d_age, d_size] if bit]
        meta_str = " &bull; ".join(meta_bits)

        img_tag = (
            f'<img src="{d_photo}" alt="{d_name} ({d_id}) - {d_breed} urgent dog at {d_shelter}" loading="lazy">'
            if d_photo
            else '<div class="no-photo">Photo on Official Shelter Listing</div>'
        )

        cards_html.append(
            f"""<article class="card">
  <div class="card-media">
    {img_tag}
    <span class="badge">{d_badge}</span>
  </div>
  <div class="card-body">
    <div class="card-head">
      <h2>{d_name}</h2>
      <span class="aid">#{d_id}</span>
    </div>
    <p class="shelter-loc">{d_shelter}</p>
    {f'<p class="meta">{meta_str}</p>' if meta_str else ''}
    {f'<span class="needs-pill">{d_needs}</span>' if d_needs else ''}
    {f'<p class="note">{d_note}</p>' if d_note else ''}
    <a class="btn" href="{d_url}" target="_blank" rel="noopener noreferrer">View Official Shelter Listing &rarr;</a>
  </div>
</article>"""
        )

        ld_items.append({
            "@type": "ListItem",
            "position": idx,
            "name": f"{dog.get('name', 'Urgent Dog')} ({dog.get('animal_id', '')}) - {dog.get('breed', 'Dog')} - {dog.get('deadline_or_urgency', 'Urgent')}",
            "url": dog.get("listing_url") or page_url,
        })

    if not cards_html:
        cards_section = (
            '<div class="empty-state">'
            '<h2>No Active Urgent Listings Right Now</h2>'
            '<p>No dogs are currently posted on this urgent feed, or the shelter board is between updates. '
            'Check back soon or <a href="/">browse all California shelters on UrgentPaws</a>.</p>'
            '</div>'
        )
    else:
        cards_section = f'<section class="grid">{"".join(cards_html)}</section>'

    faqs = build_seo_faqs(breadcrumb_label, contact, address, foster_capable)
    faq_html_blocks = "".join(
        f'<div class="faq-item"><h3>{html.escape(q)}</h3><p>{html.escape(a)}</p></div>'
        for q, a in faqs
    )

    json_ld = json.dumps(
        [
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": title,
                "description": meta_desc,
                "url": page_url,
                "dateModified": now_iso,
                "mainEntity": {
                    "@type": "ItemList",
                    "numberOfItems": len(dogs),
                    "itemListElement": ld_items,
                },
            },
            {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": a},
                    }
                    for q, a in faqs
                ],
            },
        ],
        ensure_ascii=False,
    )

    meta_grid_items = []
    if address:
        meta_grid_items.append(f"<div><strong>Location:</strong> {html.escape(address)}</div>")
    if contact:
        meta_grid_items.append(f"<div><strong>Rescue / Foster Contact:</strong> {html.escape(contact)}</div>")
    meta_grid_html = (
        f'<div class="shelter-meta-grid">{"".join(meta_grid_items)}</div>'
        if meta_grid_items
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <meta name="description" content="{html.escape(meta_desc)}">
  <link rel="canonical" href="{page_url}">
  <link rel="icon" href="logo.png" type="image/png">
  <meta property="og:title" content="{html.escape(title)}">
  <meta property="og:description" content="{html.escape(meta_desc)}">
  <meta property="og:url" content="{page_url}">
  <meta property="og:type" content="website">
  <script type="application/ld+json">{json_ld}</script>
  <style>
    :root {{
      --bg: #f5f5f4;
      --surface: #ffffff;
      --text: #171717;
      --muted: #525252;
      --border: #e5e5e5;
      --accent: #dc2626;
      --accent-soft: #fef2f2;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
    }}
    .wrap {{ max-width: 1140px; margin: 0 auto; padding: 0 1.25rem; }}
    .topbar {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 0.7rem 0;
    }}
    .topbar-inner {{
      position: relative;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
    }}
    .brand-link {{
      display: flex;
      align-items: center;
      gap: 0.55rem;
      text-decoration: none;
      color: var(--text);
    }}
    .brand-logo {{
      width: 28px;
      height: 28px;
      border-radius: 7px;
      object-fit: contain;
    }}
    .brand-title {{
      font-size: 1.15rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      line-height: 1.1;
    }}
    .brand-sub {{
      display: none;
      font-size: 0.75rem;
      color: var(--muted);
      margin-top: 2px;
    }}
    .nav-links {{
      display: none;
      gap: 1.1rem;
      align-items: center;
    }}
    .nav-links a {{
      color: #404040;
      text-decoration: none;
      font-size: 0.8rem;
      font-weight: 600;
    }}
    .nav-links a:hover {{ color: #0a0a0a; }}
    .burger-btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      border: 1px solid #d4d4d4;
      background: #ffffff;
      color: #262626;
      cursor: pointer;
      padding: 0;
    }}
    .burger-btn:hover {{ background: #f5f5f5; }}
    .mobile-drawer {{
      display: none;
      position: absolute;
      right: 1.25rem;
      top: calc(100% + 0.45rem);
      width: 176px;
      background: #ffffff;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 0.35rem 0;
      box-shadow: 0 10px 24px rgba(0,0,0,0.12);
      z-index: 40;
      flex-direction: column;
    }}
    .mobile-drawer.open {{ display: flex; }}
    .mobile-drawer a {{
      padding: 0.5rem 0.9rem;
      font-size: 0.8rem;
      font-weight: 600;
      color: #262626;
      text-decoration: none;
    }}
    .mobile-drawer a:hover {{ background: #f5f5f5; }}
    @media (min-width: 640px) {{
      .topbar {{ position: sticky; top: 0; z-index: 20; padding: 0.9rem 0; }}
      .brand-sub {{ display: block; }}
      .nav-links {{ display: flex; }}
      .burger-btn, .mobile-drawer {{ display: none !important; }}
    }}
    .hero {{ padding: 1.5rem 0 1rem; }}
    .breadcrumb {{
      font-size: 0.82rem;
      color: var(--muted);
      margin-bottom: 0.5rem;
    }}
    .breadcrumb a {{ color: var(--muted); text-decoration: none; }}
    .breadcrumb a:hover {{ text-decoration: underline; }}
    .title-row {{
      display: flex;
      align-items: baseline;
      gap: 0.65rem;
      flex-wrap: wrap;
    }}
    h1 {{
      margin: 0;
      font-size: 1.65rem;
      font-weight: 800;
      letter-spacing: -0.02em;
    }}
    .count-pill {{
      background: #ffffff;
      border: 1px solid var(--border);
      color: var(--text);
      font-size: 0.82rem;
      font-weight: 700;
      padding: 0.15rem 0.65rem;
      border-radius: 999px;
    }}
    .updated-sub {{
      margin: 0.35rem 0 0;
      font-size: 0.85rem;
      color: var(--muted);
    }}
    .shelter-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.15rem 1.35rem;
      margin: 1.1rem 0 1.5rem;
    }}
    .shelter-card p {{ margin: 0 0 0.75rem; font-size: 0.94rem; color: #262626; }}
    .shelter-card p:last-child {{ margin-bottom: 0; }}
    .shelter-meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 0.65rem 1.25rem;
      padding-top: 0.75rem;
      border-top: 1px solid var(--border);
      font-size: 0.86rem;
      color: var(--muted);
    }}
    .shelter-meta-grid strong {{ color: var(--text); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 1.25rem;
      margin-bottom: 2.5rem;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }}
    .card-media {{ position: relative; background: #e5e5e5; }}
    .card-media img {{
      width: 100%;
      height: 245px;
      object-fit: cover;
      object-position: center 20%;
      display: block;
    }}
    .no-photo {{
      height: 245px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .badge {{
      position: absolute;
      top: 0.75rem;
      left: 0.75rem;
      background: var(--accent);
      color: #ffffff;
      font-weight: 800;
      font-size: 0.72rem;
      letter-spacing: 0.02em;
      text-transform: uppercase;
      padding: 0.25rem 0.6rem;
      border-radius: 999px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.18);
    }}
    .card-body {{
      padding: 1rem 1.1rem 1.15rem;
      display: flex;
      flex-direction: column;
      flex: 1;
    }}
    .card-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 0.5rem;
    }}
    .card-head h2 {{ margin: 0; font-size: 1.15rem; font-weight: 800; }}
    .aid {{ color: var(--muted); font-size: 0.84rem; font-weight: 600; flex-shrink: 0; }}
    .shelter-loc {{ margin: 0.2rem 0 0.3rem; font-size: 0.85rem; color: var(--muted); }}
    .meta {{ margin: 0 0 0.55rem; font-size: 0.86rem; font-weight: 500; color: #262626; }}
    .needs-pill {{
      display: inline-block;
      align-self: flex-start;
      background: var(--accent-soft);
      border: 1px solid #fecaca;
      color: #991b1b;
      font-size: 0.76rem;
      font-weight: 700;
      padding: 0.2rem 0.55rem;
      border-radius: 6px;
      margin-bottom: 0.55rem;
    }}
    .note {{ margin: 0 0 0.9rem; font-size: 0.84rem; color: var(--muted); }}
    .btn {{
      margin-top: auto;
      display: block;
      text-align: center;
      background: #171717;
      color: #ffffff;
      text-decoration: none;
      padding: 0.62rem 0.9rem;
      border-radius: 999px;
      font-weight: 600;
      font-size: 0.84rem;
    }}
    .btn:hover {{ background: #262626; }}
    .empty-state {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 2.25rem 1.5rem;
      text-align: center;
      margin-bottom: 2.5rem;
    }}
    .empty-state h2 {{ margin: 0 0 0.4rem; font-size: 1.15rem; }}
    .empty-state p {{ margin: 0; color: var(--muted); font-size: 0.94rem; }}
    .empty-state a {{ color: #1d4ed8; font-weight: 600; }}
    .faq-section {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.35rem 1.5rem;
      margin-bottom: 2.5rem;
    }}
    .faq-section h2 {{ margin: 0 0 1rem; font-size: 1.15rem; font-weight: 800; }}
    .faq-item {{ padding: 0.75rem 0; border-top: 1px solid var(--border); }}
    .faq-item:first-of-type {{ border-top: none; padding-top: 0; }}
    .faq-item h3 {{ margin: 0 0 0.3rem; font-size: 0.96rem; font-weight: 700; }}
    .faq-item p {{ margin: 0; font-size: 0.88rem; color: var(--muted); }}
    footer {{
      background: var(--surface);
      border-top: 1px solid var(--border);
      padding: 1rem 0 1.5rem;
      font-size: 0.82rem;
      color: var(--muted);
    }}
    .footer-details {{
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.85rem;
      margin-bottom: 0.85rem;
    }}
    .footer-details summary {{
      cursor: pointer;
      font-weight: 700;
      color: var(--text);
      list-style: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .footer-details summary::-webkit-details-marker {{ display: none; }}
    .footer-details[open] .chevron {{ transform: rotate(180deg); }}
    .footer-dir-body {{ padding-top: 0.75rem; }}
    .footer-subhead {{
      margin: 0 0 0.35rem;
      font-size: 0.7rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #737373;
    }}
    .footer-shelters-list {{
      margin: 0 0 0.85rem;
      line-height: 1.8;
    }}
    .footer-shelters-list:last-child {{ margin-bottom: 0; }}
    .footer-shelters-list a {{
      color: var(--muted);
      text-decoration: underline;
      text-underline-offset: 3px;
    }}
    .footer-shelters-list a:hover {{ color: var(--text); }}
    .footer-bottom {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
    }}
    .footer-bottom-links {{ display: flex; gap: 1.15rem; flex-wrap: wrap; }}
    .footer-bottom-links a {{ color: var(--muted); text-decoration: none; }}
    .footer-bottom-links a:hover {{ color: var(--text); }}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="wrap topbar-inner">
      <a class="brand-link" href="/">
        <img src="logo.png" alt="UrgentPaws logo" class="brand-logo" onerror="this.style.display='none'">
        <div>
          <div class="brand-title">UrgentPaws</div>
          <div class="brand-sub">Municipal shelter dogs facing urgent deadlines, with direct IDs and shelter links.</div>
        </div>
      </a>

      <nav class="nav-links" aria-label="Primary navigation">
        <a href="/">Urgent Dogs</a>
        <a href="/about-urgent-paws">About</a>
        <a href="/for-shelters">For Shelters</a>
        <a href="/blog">Guide</a>
        <a href="/contact-us">Contact</a>
      </nav>

      <button id="mobileMenuBtn" class="burger-btn" type="button" aria-expanded="false" aria-controls="mobileNavDrawer" aria-label="Open navigation menu">
        <svg id="burgerIconOpen" width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h16" />
        </svg>
        <svg id="burgerIconClose" style="display:none" width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>

      <nav id="mobileNavDrawer" class="mobile-drawer" aria-label="Mobile navigation">
        <a href="/">Urgent Dogs</a>
        <a href="/about-urgent-paws">About</a>
        <a href="/for-shelters">For Shelters</a>
        <a href="/blog">Guide</a>
        <a href="/contact-us">Contact</a>
      </nav>
    </div>
  </header>

  <main class="wrap">
    <section class="hero">
      <div class="breadcrumb"><a href="/">Urgent Dogs</a> &rsaquo; {html.escape(breadcrumb_label)}</div>
      <div class="title-row">
        <h1>{html.escape(h1)}</h1>
        <span class="count-pill">{len(dogs)} active</span>
      </div>
      <p class="updated-sub">Auto-updated every 3 hours &bull; Last checked {html.escape(checked_label)}</p>
    </section>

    <section class="shelter-card">
      <p>{html.escape(intro)}</p>
      {meta_grid_html}
    </section>

    {cards_section}

    <section class="faq-section">
      <h2>Rescue, Foster &amp; Adoption FAQ — {html.escape(breadcrumb_label)}</h2>
      {faq_html_blocks}
    </section>
  </main>

  <footer>
    <div class="wrap">
      <details class="footer-details">
        <summary>
          <span>Browse Urgent Dogs by Shelter &amp; Category</span>
          <span class="chevron" aria-hidden="true">&#9662;</span>
        </summary>
        <div class="footer-dir-body">
          <p class="footer-subhead">By Shelter</p>
          <p class="footer-shelters-list">{footer_shelter_links}</p>
          <p class="footer-subhead">By Urgency, Foster Eligibility &amp; Breed</p>
          <p class="footer-shelters-list">{footer_intent_links}</p>
        </div>
      </details>
      <div class="footer-bottom">
        <span>&copy; 2026 UrgentPaws | Urgent Municipal Shelter Dog Directory</span>
        <div class="footer-bottom-links">
          <a href="/">Home</a>
          <a href="/about-urgent-paws">About</a>
          <a href="/for-shelters">For Shelters</a>
          <a href="/blog">Guide</a>
          <a href="/contact-us">Contact Us</a>
        </div>
      </div>
    </div>
  </footer>

  <script>
    (function () {{
      var btn = document.getElementById('mobileMenuBtn');
      var drawer = document.getElementById('mobileNavDrawer');
      var openIcon = document.getElementById('burgerIconOpen');
      var closeIcon = document.getElementById('burgerIconClose');
      function setOpen(open) {{
        if (!drawer || !btn) return;
        drawer.classList.toggle('open', open);
        if (openIcon) openIcon.style.display = open ? 'none' : 'block';
        if (closeIcon) closeIcon.style.display = open ? 'block' : 'none';
        btn.setAttribute('aria-expanded', String(open));
      }}
      if (btn && drawer) {{
        btn.addEventListener('click', function (e) {{
          e.stopPropagation();
          setOpen(!drawer.classList.contains('open'));
        }});
        document.addEventListener('click', function (e) {{
          if (drawer.classList.contains('open') && !drawer.contains(e.target) && !btn.contains(e.target)) {{
            setOpen(false);
          }}
        }});
        document.addEventListener('keydown', function (e) {{
          if (e.key === 'Escape') setOpen(false);
        }});
      }}
    }})();
  </script>
</body>
</html>
"""


def generate_static_seo_pages(unique_dogs: list[dict], shelters: list[dict], now_iso: str) -> None:
    sync_static_page_navs(shelters)
    print("Generating enriched shelter & intent SEO pages + sitemap.xml...", flush=True)
    base_url = "https://www.urgentpaws.org"
    today_date = now_iso[:10]

    try:
        pt_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Los_Angeles"))
        checked_label = pt_dt.strftime("%b %d, %Y at %I:%M %p PT")
    except Exception:
        checked_label = now_iso

   footer_shelter_links = " ".join(
        f'<a href="/{cfg["slug"]}">{html.escape(cfg["name"])}</a>'
        for cfg in shelters
    )
    footer_intent_links = " ".join(
        f'<a href="/{lander["slug"]}">{html.escape(lander["name"])}</a>'
        for lander in PROGRAMMATIC_SEO_LANDERS
    )

    for cfg in shelters:
        slug = cfg["slug"]
        page_url = f"{base_url}/{slug}"
        shelter_dogs = [
            d for d in unique_dogs
            if d.get("shelter_slug") == slug
            or (not d.get("shelter_slug") and (d.get("county") or "").lower() == cfg["county"].lower())
        ]

        page_html = render_directory_html(
            page_url=page_url,
            title=cfg["title"],
            meta_desc=cfg["meta_desc"],
            h1=cfg["h1"],
            breadcrumb_label=cfg["name"],
            intro=cfg["intro"],
            address=cfg.get("address") or "",
            contact=cfg.get("contact") or "",
            foster_capable=bool(cfg.get("foster_capable")),
            dogs=shelter_dogs,
            footer_shelter_links=footer_shelter_links,
            footer_intent_links=footer_intent_links,
            checked_label=checked_label,
            now_iso=now_iso,
        )
        with open(f"{slug}.html", "w", encoding="utf-8") as f:
            f.write(page_html)

    for lander in PROGRAMMATIC_SEO_LANDERS:
        slug = lander["slug"]
        page_url = f"{base_url}/{slug}"
        matched_dogs = [d for d in unique_dogs if lander["filter_fn"](d)]
        page_html = render_directory_html(
            page_url=page_url,
            title=lander["title"],
            meta_desc=lander["meta_desc"],
            h1=lander["h1"],
            breadcrumb_label=lander["name"],
            intro=lander["intro"],
            address="Participating California Municipal Animal Shelters",
            contact="See each dog's card for the direct shelter rescue/foster coordinator email",
            foster_capable="foster" in slug,
            dogs=matched_dogs,
            footer_shelter_links=footer_shelter_links,
            footer_intent_links=footer_intent_links,
            checked_label=checked_label,
            now_iso=now_iso,
        )
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

    for cfg in shelters:
        sitemap_urls.append(
            f"""  <url>
    <loc>{base_url}/{cfg["slug"]}</loc>
    <lastmod>{today_date}</lastmod>
    <changefreq>hourly</changefreq>
    <priority>0.85</priority>
  </url>"""
        )

    for lander in PROGRAMMATIC_SEO_LANDERS:
        sitemap_urls.append(
            f"""  <url>
    <loc>{base_url}/{lander["slug"]}</loc>
    <lastmod>{today_date}</lastmod>
    <changefreq>hourly</changefreq>
    <priority>0.8</priority>
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

    print(
        f"  Generated {len(shelters)} shelter pages + {len(PROGRAMMATIC_SEO_LANDERS)} intent SEO pages + sitemap.xml",
        flush=True,
    )


def run_shelter_scraper(page, cfg: dict, now_iso: str) -> list[dict]:
    ptype = cfg.get("portal_type")
    if ptype == "stanislaus":
        return scrape_stanislaus_red(page, cfg, now_iso)
    if ptype == "rescuegroups":
        return fetch_rescuegroups(cfg, now_iso)
    if ptype == "24petconnect":
        return scrape_24petconnect_shelter(page, cfg, now_iso)
    if ptype == "laas_petharbor":
        return scrape_laas_newhope_alerts(page, cfg, now_iso)
    if ptype == "sanjose_arcgis":
        return fetch_sanjose_arcgis(cfg, now_iso)
    if ptype == "oc_rescuetrack":
        return scrape_oc_rescuetrack(page, cfg, now_iso)
    if ptype == "lacounty_dacc":
        return scrape_lacounty_dacc(page, cfg, now_iso)
    print(f"  Unknown portal_type '{ptype}' for {cfg.get('slug')}", flush=True)
    return []


def main():
    parser = argparse.ArgumentParser(description="Fetch urgent shelter dogs into dogs.json")
    parser.add_argument(
        "--shelter",
        default="",
        help="Optional comma-separated shelter slug(s) to run (e.g., --shelter oakland-animal-services)",
    )
    parser.add_argument(
        "--only-oas",
        action="store_true",
        help="Alias for --shelter oakland-animal-services",
    )
    args = parser.parse_args()

    all_shelters = load_shelters()
    shelters_by_slug = {s["slug"]: s for s in all_shelters}
    shelters_by_county = {s["county"].lower(): s["slug"] for s in all_shelters if s.get("county")}

    target_slugs = set()
    if args.only_oas:
        target_slugs.add("oakland-animal-services")
    if args.shelter:
        target_slugs.update(s.strip() for s in args.shelter.split(",") if s.strip())

    if target_slugs:
        unknown = target_slugs - set(shelters_by_slug.keys())
        if unknown:
            raise SystemExit(f"Unknown shelter slug(s): {', '.join(sorted(unknown))}")
        selected_shelters = [s for s in all_shelters if s["slug"] in target_slugs]
        print(f"Running targeted scrape for: {', '.join(sorted(target_slugs))}...", flush=True)
    else:
        selected_shelters = all_shelters

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    previous_dogs = load_previous_dogs()
    all_dogs = []

    api_shelters = [s for s in selected_shelters if s.get("portal_type") not in BROWSER_PORTAL_TYPES]
    browser_shelters = [s for s in selected_shelters if s.get("portal_type") in BROWSER_PORTAL_TYPES]

    browser = None
    page = None
    pw_ctx = None
    api_pool = ThreadPoolExecutor(max_workers=max(1, min(8, len(api_shelters)))) if api_shelters else None
    api_futures = [api_pool.submit(run_shelter_scraper, None, cfg, now_iso) for cfg in api_shelters] if api_pool else []

    try:
        if browser_shelters:
            pw_ctx = sync_playwright().start()
            browser = pw_ctx.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 1800})
            context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in BLOCKED_RESOURCE_TYPES
                else route.continue_(),
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()

            for cfg in browser_shelters:
                all_dogs.extend(run_shelter_scraper(page, cfg, now_iso))
    finally:
        if browser:
            browser.close()
        if pw_ctx:
            pw_ctx.stop()
        if api_pool:
            for fut in api_futures:
                try:
                    all_dogs.extend(fut.result())
                except Exception as exc:
                    print(f"  API scraper error: {exc}", flush=True)
            api_pool.shutdown(wait=True)

    # Preserve previous shelter records if a shelter wasn't targeted or returned 0
    active_slugs = {d.get("shelter_slug") for d in all_dogs if d.get("shelter_slug")}
    stale_dropped = 0
    for prev in previous_dogs:
        slug = infer_shelter_slug(prev, shelters_by_county)
        prev["shelter_slug"] = slug
        if slug and slug not in active_slugs:
            if not target_slugs and is_preserved_record_stale(prev, now_utc):
                stale_dropped += 1
                continue
            if slug == "contra-costa-animal-services" and prev.get("animal_id"):
                aid = prev["animal_id"].upper()
                prev["id"] = f"24pc-ccst-{aid.lower()}"
                prev["listing_url"] = f"https://24petconnect.com/CCASDTransferPreferredPets/Details/CCST/{aid}"
            all_dogs.append(prev)

    seen = set()
    unique_dogs = []
    expired_dropped = 0
    for dog in all_dogs:
        if NON_DOG_PATTERN.search(f"{dog.get('breed') or ''} {dog.get('name') or ''}"):
            continue

        slug = infer_shelter_slug(dog, shelters_by_county)
        shelter_cfg = shelters_by_slug.get(slug)
        tz_name = shelter_cfg.get("timezone", "America/Los_Angeles") if shelter_cfg else "America/Los_Angeles"

        is_live_sbct = (
            slug == "san-bernardino-city-animal-shelter"
            and dog.get("last_checked") == now_iso
        )
        if not is_live_sbct and is_deadline_expired(dog, tz_name, now_utc):
            expired_dropped += 1
            continue

        dedup_scope = slug or (dog.get("county") or "").lower()
        key = f"{dedup_scope}-{dog.get('animal_id')}".lower()
        if key not in seen:
            seen.add(key)
            unique_dogs.append(normalize_dog_record(dog, shelter_cfg))

    if expired_dropped or stale_dropped:
        print(
            f"  Pruned {expired_dropped} passed-deadline dogs and {stale_dropped} stale (>7d) preserved dogs.",
            flush=True,
        )

    def sort_rank(d: dict) -> tuple:
        badge = (d.get("deadline_or_urgency") or "").upper()
        if any(k in badge for k in ("EUTHANASIA", "FINAL CALL", "DEADLINE", "OUT OF TIME", "NEEDS RESCUE FINAL")):
            tier = 0
        elif any(k in badge for k in ("URGENT", "RESCUE", "RED", "ALERT", "PRIORITY", "MEDICAL", "AP-KHOA")):
            tier = 1
        elif any(k in badge for k in ("GREATEST RISK", "TRANSFER")):
            tier = 2
        else:
            tier = 3
        return (tier, d.get("state", ""), d.get("county", ""), d.get("name", ""))

    unique_dogs.sort(key=sort_rank)

    with open("dogs.json", "w", encoding="utf-8") as f:
        json.dump(unique_dogs, f, indent=2)
        f.write("\n")

    generate_static_seo_pages(unique_dogs, all_shelters, now_iso)
    print(f"TOTAL VERIFIED MUNICIPAL URGENT DOGS SAVED: {len(unique_dogs)}", flush=True)


if __name__ == "__main__":
    main()
