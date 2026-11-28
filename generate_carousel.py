import html
import json
import os
import re
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

PT_TZ = ZoneInfo("America/Los_Angeles")
HISTORY_FILE = "reel_history.json"
OUTPUT_DIR = "carousel_slides"

PLACEHOLDER_NAMES = {
    "laas", "unknown", "unnamed", "no name", "n/a", "na", "none",
    "dog", "urgent dog", "shelter dog", "available", "stray",
}


def extract_display_name(dog: dict) -> tuple[str, bool]:
    """Returns (clean_display_name, is_fallback_name), ignoring IDs and shelter acronyms."""
    aid = (dog.get("animal_id") or "").strip()
    raw_name = (dog.get("name") or "").strip()
    clean_name = (
        re.sub(rf"#?\s*{re.escape(aid)}\b", "", raw_name, flags=re.IGNORECASE).strip(" -–|:")
        if aid
        else raw_name
    )
    if clean_name and clean_name.lower() not in PLACEHOLDER_NAMES and not clean_name.isdigit():
        return clean_name, False
    return (f"Urgent Dog #{aid}" if aid else "Urgent Shelter Dog"), True


def parse_deadline_days_away(dog: dict) -> int | None:
    """Returns days from today (PT) until an explicit calendar deadline."""
    text = " ".join(
        str(dog.get(k) or "")
        for k in ("deadline_or_urgency", "deadline", "exit_date", "euthanasia_date", "transport_note")
    ).strip()
    if not text:
        return None

    today_pt = datetime.now(PT_TZ).date()

    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", text)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return (datetime(year, month, day, tzinfo=PT_TZ).date() - today_pt).days
        except ValueError:
            pass

    m_iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if m_iso:
        try:
            d_iso = datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)), tzinfo=PT_TZ).date()
            return (d_iso - today_pt).days
        except ValueError:
            pass

    m_short = re.search(r"\b(?:DEADLINE|DUE|EXIT|BY)\s+(\d{1,2})/(\d{1,2})\b", text, re.IGNORECASE)
    if m_short:
        month, day = int(m_short.group(1)), int(m_short.group(2))
        try:
            candidate = datetime(today_pt.year, month, day, tzinfo=PT_TZ).date()
            if (candidate - today_pt).days < -180:
                candidate = datetime(today_pt.year + 1, month, day, tzinfo=PT_TZ).date()
            return (candidate - today_pt).days
        except ValueError:
            pass

    return None


def load_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {"dog_ids": [], "shelters": []}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return {"dog_ids": data, "shelters": []}
            if isinstance(data, dict):
                return {
                    "dog_ids": data.get("dog_ids", []),
                    "shelters": data.get("shelters", []),
                }
    except Exception:
        pass
    return {"dog_ids": [], "shelters": []}


def save_history_batch(dogs: list[dict]) -> None:
    hist = load_history()
    ids = list(hist["dog_ids"])
    shelters = list(hist["shelters"])

    for d in dogs:
        dog_id = d.get("id") or d.get("animal_id") or ""
        shelter = (d.get("shelter") or "").strip().lower()
        if dog_id:
            ids = [x for x in ids if x != dog_id]
            ids.append(dog_id)
        if shelter:
            shelters.append(shelter)

    hist["dog_ids"] = ids[-60:]
    hist["shelters"] = shelters[-20:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=2)
        f.write("\n")


def clean_shelter_label(shelter: str, city: str, aid: str = "") -> str:
    s = re.sub(r"\s+", " ", (shelter or "California Municipal Shelter")).strip()
    s = re.sub(r"\(?\s*Success Story.*$", "", s, flags=re.IGNORECASE).strip()
    if aid:
        s = re.sub(rf"\s*#?\s*{re.escape(aid)}\b", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\(\s*\)", "", s).strip()
    if city and city.lower() not in s.lower() and "(" not in s:
        s = f"{s} ({city}, CA)"
    return s


def select_carousel_dogs(count: int = 5) -> list[dict]:
    if not os.path.exists("dogs.json"):
        raise FileNotFoundError("dogs.json not found. Run fetch_dogs.py first.")

    with open("dogs.json", "r", encoding="utf-8") as f:
        dogs = json.load(f)

    if not isinstance(dogs, list) or not dogs:
        raise ValueError("dogs.json is empty.")

    hist = load_history()
    recently_featured = set(hist["dog_ids"])
    recent_shelters = set(hist["shelters"][-5:])

    valid_dogs = []
    for d in dogs:
        days_away = parse_deadline_days_away(d)
        if days_away is not None and days_away < 0:
            continue
        valid_dogs.append((d, days_away))

    pool = valid_dogs if valid_dogs else [(d, None) for d in dogs]

    def base_rank_key(item: tuple[dict, int | None]) -> tuple:
        d, days_away = item
        dog_uid = d.get("id") or d.get("animal_id") or ""
        already_used = 1 if dog_uid in recently_featured else 0
        has_photo = 0 if (d.get("photo_url") or "").startswith("http") else 1
        shelter_key = (d.get("shelter") or "").strip().lower()
        recent_shelter_penalty = 1 if shelter_key in recent_shelters else 0
        _, is_fallback_name = extract_display_name(d)
        has_real_name = 1 if is_fallback_name else 0

        if days_away in (0, 1, 2):
            urgency_bucket = 0
            day_tiebreak = days_away
        elif days_away in (3, 4):
            urgency_bucket = 1
            day_tiebreak = days_away
        elif any(
            k in (d.get("deadline_or_urgency") or "").upper()
            for k in ("FINAL CALL", "EUTHANASIA", "OUT OF TIME", "RED LIST")
        ):
            urgency_bucket = 2
            day_tiebreak = 0
        else:
            urgency_bucket = 3
            day_tiebreak = days_away if days_away is not None else 999

        return (
            already_used,
            has_photo,
            urgency_bucket,
            recent_shelter_penalty,
            has_real_name,
            day_tiebreak,
        )

    pool.sort(key=base_rank_key)

    # Pick top dogs while spreading across distinct shelters first
    chosen: list[dict] = []
    used_shelters_in_batch: set[str] = set()
    remainder: list[dict] = []

    for d, _ in pool:
        if len(chosen) >= count:
            break
        s_key = (d.get("shelter") or "").strip().lower()
        if s_key and s_key not in used_shelters_in_batch:
            chosen.append(d)
            used_shelters_in_batch.add(s_key)
        else:
            remainder.append(d)

    for d in remainder:
        if len(chosen) >= count:
            break
        chosen.append(d)

    save_history_batch(chosen)
    return chosen


def base_page_shell(body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=1080, initial-scale=1" />
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{
      width: 1080px;
      height: 1350px;
      overflow: hidden;
      background: #0f172a;
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }}
  </style>
</head>
<body>
{body_html}
</body>
</html>
"""


def build_cover_slide_html(dogs: list[dict]) -> str:
    today_label = datetime.now(PT_TZ).strftime("%b %d, %Y")
    photos = [d.get("photo_url") for d in dogs if (d.get("photo_url") or "").startswith("http")][:4]
    while len(photos) < 4:
        photos.append(photos[0] if photos else "")

    grid_cells = "".join(
        f'<div class="cell"><img src="{html.escape(p)}" alt="" /></div>' if p else '<div class="cell empty"></div>'
        for p in photos
    )

    shelters = []
    for d in dogs:
        s = (d.get("county") or d.get("city") or "").strip()
        if s and s not in shelters:
            shelters.append(s)
    region_line = " • ".join(shelters[:4]) if shelters else "California Municipal Shelters"

    return base_page_shell(f"""
  <style>
    .cover {{
      position: relative;
      width: 1080px;
      height: 1350px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      padding: 64px;
      background: #0f172a;
    }}
    .collage {{
      position: absolute;
      inset: 0;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 6px;
      opacity: 0.42;
    }}
    .cell {{ overflow: hidden; background: #1e293b; }}
    .cell img {{ width: 100%; height: 100%; object-fit: cover; object-position: center 22%; }}
    .vignette {{
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(15,23,42,0.86) 0%, rgba(15,23,42,0.52) 45%, rgba(15,23,42,0.96) 100%);
    }}
    .top-bar {{
      position: relative;
      z-index: 10;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .pill-red {{
      background: #dc2626;
      color: #fff;
      font-size: 26px;
      font-weight: 900;
      padding: 12px 26px;
      border-radius: 999px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .pill-date {{
      background: rgba(15, 23, 42, 0.85);
      border: 2px solid rgba(255,255,255,0.25);
      color: #f8fafc;
      font-size: 26px;
      font-weight: 700;
      padding: 10px 22px;
      border-radius: 999px;
    }}
    .hero-copy {{
      position: relative;
      z-index: 10;
      margin-top: auto;
    }}
    .kicker {{
      font-size: 30px;
      font-weight: 800;
      color: #fca5a5;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-bottom: 16px;
    }}
    h1 {{
      font-size: 84px;
      font-weight: 900;
      line-height: 1.03;
      letter-spacing: -0.025em;
      color: #ffffff;
    }}
    .sub {{
      margin-top: 24px;
      font-size: 34px;
      line-height: 1.35;
      color: #e2e8f0;
      font-weight: 600;
    }}
    .swipe-bar {{
      margin-top: 40px;
      background: #dc2626;
      border-radius: 20px;
      padding: 24px 32px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 32px;
      font-weight: 900;
    }}
  </style>
  <div class="cover">
    <div class="collage">{grid_cells}</div>
    <div class="vignette"></div>
    <div class="top-bar">
      <span class="pill-red">California Red List</span>
      <span class="pill-date">{html.escape(today_label)}</span>
    </div>
    <div class="hero-copy">
      <div class="kicker">Time-Sensitive Shelter Alert</div>
      <h1>{len(dogs)} Urgent Shelter Dogs Need Help Now</h1>
      <p class="sub">{html.escape(region_line)}</p>
      <div class="swipe-bar">
        <span>Swipe for IDs, deadlines &amp; shelters</span>
        <span>→</span>
      </div>
    </div>
  </div>
""")


def build_dog_slide_html(dog: dict, index: int, total: int) -> str:
    aid = (dog.get("animal_id") or "").strip()
    display_name, is_fallback_name = extract_display_name(dog)
    shelter = clean_shelter_label(dog.get("shelter") or "", dog.get("city") or "", aid)
    badge = dog.get("deadline_or_urgency") or "URGENT DEADLINE"
    needs = dog.get("needs_type") or "Needs Immediate Rescue, Foster, or Adopter"
    note = (dog.get("transport_note") or "").strip()
    photo = (dog.get("photo_url") or "").strip()
    meta = " • ".join(
        html.escape(x)
        for x in [dog.get("breed", ""), dog.get("age", ""), dog.get("size", "")]
        if x
    )

    if photo:
        escaped_photo = html.escape(photo)
        img_block = f"""
        <img class="bg-blur" src="{escaped_photo}" alt="" />
        <img class="fg-dog" src="{escaped_photo}" alt="{html.escape(display_name)}" />
        """
    else:
        img_block = '<div class="no-photo">Photo on Official Shelter Listing</div>'

    return base_page_shell(f"""
  <style>
    .card {{
      position: relative;
      width: 1080px;
      height: 1350px;
      display: flex;
      flex-direction: column;
      background: #0f172a;
    }}
    .photo-area {{
      position: relative;
      width: 1080px;
      height: 760px;
      overflow: hidden;
      background: #1e293b;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .bg-blur {{
      position: absolute;
      inset: -30px;
      width: calc(100% + 60px);
      height: calc(100% + 60px);
      object-fit: cover;
      filter: blur(24px) brightness(0.45);
    }}
    .fg-dog {{
      position: relative;
      z-index: 2;
      width: 100%;
      height: 100%;
      object-fit: contain;
      object-position: center center;
    }}
    .no-photo {{
      color: #cbd5e1;
      font-size: 30px;
      font-weight: 600;
    }}
    .top-overlay {{
      position: absolute;
      top: 32px;
      left: 36px;
      right: 36px;
      z-index: 10;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }}
    .deadline-pill {{
      background: #dc2626;
      color: #ffffff;
      font-size: 25px;
      font-weight: 900;
      padding: 11px 22px;
      border-radius: 999px;
      text-transform: uppercase;
      box-shadow: 0 6px 20px rgba(0,0,0,0.45);
    }}
    .counter-pill {{
      background: rgba(15, 23, 42, 0.85);
      border: 2px solid rgba(255,255,255,0.25);
      color: #f8fafc;
      font-size: 24px;
      font-weight: 800;
      padding: 10px 20px;
      border-radius: 999px;
      flex-shrink: 0;
    }}
    .details {{
      flex: 1;
      padding: 38px 48px 44px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      background: #0f172a;
      border-top: 2px solid rgba(255,255,255,0.12);
    }}
    .title-row {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 16px;
    }}
    h2 {{
      font-size: 56px;
      font-weight: 900;
      line-height: 1.05;
      color: #ffffff;
    }}
    .aid {{
      font-size: 32px;
      font-weight: 800;
      color: #93c5fd;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      flex-shrink: 0;
    }}
    .shelter {{
      margin-top: 10px;
      font-size: 31px;
      font-weight: 700;
      color: #f8fafc;
    }}
    .meta {{
      margin-top: 8px;
      font-size: 27px;
      color: #cbd5e1;
      font-weight: 500;
    }}
    .tags {{
      margin-top: 18px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .needs-pill {{
      background: rgba(220, 38, 38, 0.22);
      border: 2px solid #ef4444;
      color: #fecaca;
      font-size: 25px;
      font-weight: 800;
      padding: 9px 18px;
      border-radius: 12px;
    }}
    .note {{
      margin-top: 14px;
      font-size: 24px;
      color: #e2e8f0;
      line-height: 1.32;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .footer-strip {{
      margin-top: 18px;
      padding-top: 16px;
      border-top: 1px solid rgba(255,255,255,0.15);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 24px;
      color: #94a3b8;
      font-weight: 700;
    }}
    .footer-strip strong {{
      color: #ffffff;
    }}
  </style>
  <div class="card">
    <div class="photo-area">
      {img_block}
      <div class="top-overlay">
        <span class="deadline-pill">{html.escape(badge)}</span>
        <span class="counter-pill">{index} of {total}</span>
      </div>
    </div>
    <div class="details">
      <div>
        <div class="title-row">
          <h2>{html.escape(display_name)}</h2>
          {f'<span class="aid">#{html.escape(aid)}</span>' if (aid and not is_fallback_name) else ''}
        </div>
        <p class="shelter">{html.escape(shelter)}</p>
        {f'<p class="meta">{meta}</p>' if meta else ''}
        <div class="tags">
          <span class="needs-pill">{html.escape(needs)}</span>
        </div>
        {f'<p class="note">{html.escape(note)}</p>' if note else ''}
      </div>
      <div class="footer-strip">
        <span>Search ID at <strong>urgentpaws.org</strong></span>
        <span>Swipe →</span>
      </div>
    </div>
  </div>
""")


def build_cta_slide_html() -> str:
    return base_page_shell("""
  <style>
    .cta-slide {{
      width: 1080px;
      height: 1350px;
      padding: 72px;
      background: #0f172a;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .badge {{
      align-self: flex-start;
      background: rgba(220, 38, 38, 0.22);
      border: 2px solid #ef4444;
      color: #fecaca;
      font-size: 26px;
      font-weight: 800;
      padding: 10px 24px;
      border-radius: 999px;
      text-transform: uppercase;
    }}
    h2 {{
      font-size: 74px;
      font-weight: 900;
      line-height: 1.06;
      letter-spacing: -0.02em;
      margin-top: 28px;
    }}
    .steps {{
      margin-top: 44px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }}
    .step {{
      background: #1e293b;
      border: 1px solid rgba(255,255,255,0.14);
      border-radius: 22px;
      padding: 28px 32px;
    }}
    .step strong {{
      display: block;
      font-size: 32px;
      font-weight: 800;
      color: #ffffff;
      margin-bottom: 8px;
    }}
    .step span {{
      font-size: 27px;
      color: #cbd5e1;
      line-height: 1.35;
    }}
    .url-box {{
      background: #dc2626;
      border-radius: 24px;
      padding: 32px 36px;
      text-align: center;
      box-shadow: 0 14px 34px rgba(220, 38, 38, 0.35);
    }}
    .url-box small {{
      display: block;
      font-size: 26px;
      font-weight: 700;
      color: #fee2e2;
      margin-bottom: 8px;
    }}
    .url-box strong {{
      font-size: 46px;
      font-weight: 900;
      letter-spacing: -0.01em;
    }}
  </style>
  <div class="cta-slide">
    <div>
      <span class="badge">Live Directory • Updated Every 3 Hours</span>
      <h2>How to Help These Dogs Today</h2>
      <div class="steps">
        <div class="step">
          <strong>1. Copy the Animal ID (A#)</strong>
          <span>Use the exact ID on each slide so shelter staff and rescues can pull the right record fast.</span>
        </div>
        <div class="step">
          <strong>2. Check Live Status &amp; Direct Contacts</strong>
          <span>Look up any dog on UrgentPaws for official shelter listing URLs and 501(c)(3) rescue desk emails.</span>
        </div>
        <div class="step">
          <strong>3. Share to Your Story &amp; Tag Rescues</strong>
          <span>One reshare puts these dogs in front of local California fosters before deadlines pass.</span>
        </div>
      </div>
    </div>
    <div class="url-box">
      <small>Search all 11 California shelter systems free at</small>
      <strong>urgentpaws.org</strong>
    </div>
  </div>
""")


def write_carousel_caption(dogs: list[dict]) -> None:
    today_short = datetime.now(PT_TZ).strftime("%b %d")
    dog_lines = []
    for i, d in enumerate(dogs, start=1):
        aid = (d.get("animal_id") or "").strip()
        display_name, is_fallback = extract_display_name(d)
        label = display_name if is_fallback else (f"{display_name} (#{aid})" if aid else display_name)
        shelter = clean_shelter_label(d.get("shelter") or "", d.get("city") or "", aid)
        badge = d.get("deadline_or_urgency") or "Urgent"
        dog_uid = d.get("id") or aid
        link = f"https://www.urgentpaws.org/?dog={dog_uid}" if dog_uid else "https://www.urgentpaws.org"
        dog_lines.append(f"{i}. {label} — {shelter} [{badge}]\n   {link}")

    lines = [
        f"CALIFORNIA URGENT SHELTER LIST ({today_short}) — Swipe to see {len(dogs)} dogs facing immediate deadlines.",
        "",
        *dog_lines,
        "",
        "Save this post or share to your Story to help connect these dogs with California fosters and 501(c)(3) rescues.",
        "",
        "#UrgentDogs #CaliforniaShelterDogs #LAAnimalServices #RiversideShelterDogs #FosterSavesLives #AdoptDontShop",
    ]

    with open("carousel_caption.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def render_carousel(dogs: list[dict]) -> list[str]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for old in os.listdir(OUTPUT_DIR):
        if old.endswith(".png"):
            os.remove(os.path.join(OUTPUT_DIR, old))

    slides: list[tuple[str, str]] = [
        ("carousel_01_cover.png", build_cover_slide_html(dogs)),
    ]
    for idx, dog in enumerate(dogs, start=1):
        slides.append((f"carousel_{idx + 1:02d}_dog.png", build_dog_slide_html(dog, idx, len(dogs))))
    slides.append((f"carousel_{len(dogs) + 2:02d}_cta.png", build_cta_slide_html()))

    saved_paths: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1080, "height": 1350},
            device_scale_factor=1,
        )
        page = context.new_page()

        for filename, slide_html in slides:
            html_path = os.path.join(tmpdir, filename.replace(".png", ".html"))
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(slide_html)

            page.goto(f"file://{html_path}", wait_until="networkidle", timeout=30000)
            out_path = os.path.join(OUTPUT_DIR, filename)
            page.screenshot(path=out_path, type="png")
            saved_paths.append(out_path)
            print(f"Rendered {out_path}")

        context.close()
        browser.close()

    return saved_paths


def main() -> None:
    dogs = select_carousel_dogs(count=5)
    paths = render_carousel(dogs)
    write_carousel_caption(dogs)
    print(f"Generated {len(paths)} carousel slides in {OUTPUT_DIR}/ and wrote carousel_caption.txt.")


if __name__ == "__main__":
    main()
