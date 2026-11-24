import json
import os
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
    print("[1/8] Scraping Stanislaus County RED / Final Call dogs...")
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
# 2. OAKLAND ANIMAL SERVICES - /rescue-needed/ (Direct + Proxy + Shelterluv)
# ---------------------------------------------------------------------------
def parse_oas_card(href: str, txt: str, img: str, heading: str, now_iso: str) -> dict | None:
    if NON_DOG_PATTERN.search(txt):
        return None

    id_match = re.search(r"animalid=(\d+)", href, re.IGNORECASE) or re.search(r"(\d{6,9})", href)
    aid = id_match.group(1) if id_match else re.sub(r"\W+", "", (heading or "oas"))[:12]

    date_match = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", txt)
    is_priority = bool(re.search(r"\bPRIORITY\b", txt, re.IGNORECASE))
    is_out_of_time = bool(re.search(r"OUT OF TIME", txt, re.IGNORECASE))

    if is_out_of_time:
        badge = "OUT OF TIME"
        urgency_type = "OAS Out of Time (Immediate Exit)"
    elif date_match:
        badge = f"DEADLINE {date_match.group(1)}"
        urgency_type = f"OAS Exit Deadline ({date_match.group(1)})"
    elif is_priority:
        badge = "OAS PRIORITY"
        urgency_type = "OAS Priority Urgent Dog"
    else:
        badge = "OAS Rescue Needed"
        urgency_type = "OAS Rescue Needed / At Risk"

    lines = [
        clean_space(ln)
        for ln in txt.splitlines()
        if clean_space(ln)
        and not re.match(r"^(more info\.{0,3}|priority|\d{1,2}/\d{1,2}/\d{2,4})$", clean_space(ln), re.IGNORECASE)
    ]

    name = clean_space(heading or "")
    if not name or re.match(r"^(priority|\d{1,2}/\d{1,2}/\d{2,4})$", name, re.IGNORECASE):
        name = lines[0] if lines else f"OAS #{aid}"

    meta_line = " ".join(ln for ln in lines if ln != name)
    age_match = re.search(r"\b(Puppy|Young|Adult|Senior)\b", meta_line, re.IGNORECASE)
    weight_match = re.search(r"\b(\d+\s*lbs?)\b", meta_line, re.IGNORECASE)
    breed_match = re.search(r"\b(?:Male|Female),\s*(.+)$", meta_line, re.IGNORECASE)

    return {
        "id": f"oas-{aid}",
        "animal_id": aid,
        "name": name,
        "breed": clean_space(breed_match.group(1)).rstrip(".") if breed_match else "",
        "shelter": "Oakland Animal Services",
        "city": "Oakland",
        "county": "Alameda",
        "state": "CA",
        "size": weight_match.group(1) if weight_match else "",
        "age": age_match.group(1).title() if age_match else "",
        "urgency_type": urgency_type,
        "deadline_or_urgency": badge,
        "needs_type": "Rescue Pull, Foster, or Adopt",
        "transport_note": meta_line[:140],
        "photo_url": img or "",
        "listing_url": href,
        "updated_at": now_iso,
        "last_checked": now_iso,
    }


def extract_oas_cards_from_page(page) -> list[dict]:
    raw_cards = []
    seen_hrefs = set()

    for frame in page.frames:
        try:
            cards = frame.evaluate(
                """() => {
                    const out = [];
                    const links = Array.from(document.querySelectorAll("a[href]"))
                        .filter(a => /more info/i.test(a.innerText || "") || /animalId=/i.test(a.href || ""));

                    for (const a of links) {
                        let card = a.parentElement;
                        while (card && card !== document.body) {
                            const hasImg = !!card.querySelector("img");
                            const moreInfoCount = Array.from(card.querySelectorAll("a"))
                                .filter(el => /more info/i.test(el.innerText || "")).length;
                            if (hasImg && moreInfoCount <= 1) break;
                            card = card.parentElement;
                        }
                        if (!card || card === document.body) card = a.parentElement || a;

                        const img = card.querySelector("img");
                        const heading = card.querySelector("h1, h2, h3, h4, strong, b, [class*='name'], [class*='title']");
                        out.push({
                            href: a.href || "",
                            img: img ? (img.src || img.getAttribute("data-src") || "") : "",
                            heading: heading ? (heading.innerText || "").trim() : "",
                            text: (card.innerText || "").trim()
                        });
                    }
                    return out;
                }"""
            )
        except Exception:
            continue

        for c in cards:
            href = (c.get("href") or "").strip()
            if not href or href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            raw_cards.append(c)

    return raw_cards


def fetch_oas_via_proxy_and_embed(page, target_url: str, now_iso: str) -> list[dict]:
    print("  OAS direct request blocked (403); checking public relays for Shelterluv embed...")
    encoded = urllib.parse.quote(target_url, safe="")
    proxy_candidates = [
        f"https://api.codetabs.com/v1/proxy?quest={encoded}",
        f"https://corsproxy.io/?{encoded}",
        f"https://api.allorigins.win/raw?url={encoded}",
        f"https://r.jina.ai/{target_url}",
    ]

    fetched_blobs = []
    for p_url in proxy_candidates:
        host = p_url.split("/")[2]
        try:
            req = urllib.request.Request(p_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if body and "403 ERROR" not in body[:400]:
                    print(f"  OAS relay succeeded via {host} ({len(body)} chars)")
                    fetched_blobs.append(body)
        except Exception as exc:
            print(f"  OAS relay failed ({host}): {exc}")

    # 1. Extract Shelterluv iframe/script embed URL from the page HTML and load it directly in Playwright
    for blob in fetched_blobs:
        iframe_match = re.search(
            r"""https?://(?:www\.|new\.)?shelterluv\.com/[^"'\s<>\\]+""",
            blob,
            re.IGNORECASE,
        )
        if iframe_match:
            embed_url = iframe_match.group(0).replace("&amp;", "&").replace("\\/", "/")
            print(f"  Found OAS Shelterluv embed URL: {embed_url}")
            try:
                page.goto(embed_url, wait_until="networkidle", timeout=35000)
                page.wait_for_timeout(3500)
                raw_cards = extract_oas_cards_from_page(page)
                if raw_cards:
                    results = [
                        d for c in raw_cards
                        if (d := parse_oas_card(c["href"], c["text"], c.get("img") or "", c.get("heading") or "", now_iso))
                    ]
                    print(f"  OAS Shelterluv embed dogs saved: {len(results)}")
                    return results
            except Exception as exc:
                print(f"  OAS Shelterluv embed load error: {exc}")

    # 2. Otherwise parse any animalId links directly from the fetched proxy HTML
    results = []
    seen_hrefs = set()
    for blob in fetched_blobs:
        for m in re.finditer(r"https?://[^\s\"'<>)]+animalId=\d+[^\s\"'<>)]*", blob, re.IGNORECASE):
            href = m.group(0).replace("&amp;", "&").strip()
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            chunk = blob[max(0, m.start() - 500):min(len(blob), m.end() + 250)]
            img_m = re.search(r"https?://[^\s\"'<>)]+\.(?:jpg|jpeg|png|webp)", chunk, re.IGNORECASE)
            img = img_m.group(0) if img_m else ""
            plain = clean_space(re.sub(r"<[^>]+>", " ", chunk))
            dog = parse_oas_card(href, plain, img, "", now_iso)
            if dog:
                results.append(dog)

    if not results:
        sample = clean_space(fetched_blobs[0])[:220] if fetched_blobs else "all relays blocked by CloudFront"
        print(f"  OAS diagnostic -> {sample}")
    else:
        print(f"  OAS proxy dogs saved: {len(results)}")

    return results


def scrape_oas_rescue_needed(page, now_iso: str) -> list[dict]:
    url = "https://www.oaklandanimalservices.org/rescue-needed/"
    print("[2/8] Scraping Oakland Animal Services /rescue-needed/...")

    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=35000)
        status = resp.status if resp else None
        if status == 403:
            return fetch_oas_via_proxy_and_embed(page, url, now_iso)
        try:
            page.wait_for_selector("a[href*='animalId=']", timeout=10000)
        except Exception:
            page.wait_for_timeout(3000)
    except Exception as exc:
        print(f"  OAS direct load error: {exc}")
        return fetch_oas_via_proxy_and_embed(page, url, now_iso)

    raw_cards = extract_oas_cards_from_page(page)
    if not raw_cards:
        return fetch_oas_via_proxy_and_embed(page, url, now_iso)

    results = []
    for c in raw_cards:
        dog = parse_oas_card(c["href"], c["text"], c.get("img") or "", c.get("heading") or "", now_iso)
        if dog:
            results.append(dog)

    print(f"  OAS /rescue-needed/ dogs saved: {len(results)}")
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

        # Build valid 24Petconnect detail URLs per shelter portal
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
# 7. LA ANIMAL SERVICES (6 CITY SHELTERS) - NEW HOPE ALERT BOARDS
# ---------------------------------------------------------------------------
def scrape_laas_newhope_alerts(page, now_iso: str) -> list[dict]:
    print("[7/8] Scraping LA Animal Services New Hope Alert boards (Euth/Urgent/Orange/Blue)...")
    results = []
    seen_ids = set()

    for board in LAAS_ALERT_BOARDS:
        try:
            page.goto(board["url"], wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"  {board['label']} error: {exc}")
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
                if name_clean and len(name_clean) < 35:
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
                "needs_type": "New Hope Rescue Pull / Foster / Adopt",
                "transport_note": f"Active on {board['label']}",
                "photo_url": r.get("img") or "",
                "listing_url": href or f"https://24petconnect.com/LACT/Details/LACT/{animal_id}",
                "updated_at": now_iso,
                "last_checked": now_iso,
            })
            board_count += 1

        print(f"  {board['label']}: {board_count} dogs")

    print(f"  LAAS total alert dogs saved: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# 8. SAN JOSE ACS - ARCGIS REST JSON API (Staff-Flagged Rescue Priority Only)
# ---------------------------------------------------------------------------
def fetch_sanjose_arcgis(now_iso: str) -> list[dict]:
    print("[8/8] Querying San Jose ACS ArcGIS MapServer Layer 1 (Strict Filter)...")
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

        if has_rescue_priority:
            badge = rescue_priority[:28]
            urgency_type = f"SJ ACS {rescue_priority}"
            needs = "Rescue Pull / Foster"
        elif has_rescue_status:
            badge = f"SJ Rescue: {rescue_status[:24]}"
            urgency_type = f"SJ ACS Rescue Needed ({rescue_status})"
            needs = "Rescue Pull / Foster"
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

    print(f"  San Jose ArcGIS strict urgent/priority dogs saved: {len(results)}")
    return results


def main():
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    previous_dogs = load_previous_dogs()
    all_dogs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 1800})
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

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

    all_dogs.extend(fetch_sanjose_arcgis(now_iso))

    # Preserve previous county records if a shelter site temporarily returned 0 on this run
    active_counties = {d.get("county") for d in all_dogs if d.get("county")}
    for prev in previous_dogs:
        c = prev.get("county")
        if c and c not in active_counties:
            all_dogs.append(prev)

    seen = set()
    unique_dogs = []
    for dog in all_dogs:
        key = f"{dog.get('county')}-{dog.get('animal_id')}".lower()
        if key not in seen:
            seen.add(key)
            unique_dogs.append(dog)

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

    print(f"TOTAL VERIFIED MUNICIPAL URGENT DOGS SAVED: {len(unique_dogs)}")


if __name__ == "__main__":
    main()
