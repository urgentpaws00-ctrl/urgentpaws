import glob
import html
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

PT_TZ = ZoneInfo("America/Los_Angeles")
HISTORY_FILE = "reel_history.json"

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

    # 1. Match explicit MM/DD/YYYY or MM/DD/YY
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", text)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return (datetime(year, month, day, tzinfo=PT_TZ).date() - today_pt).days
        except ValueError:
            pass

    # 2. Match ISO YYYY-MM-DD
    m_iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if m_iso:
        try:
            d_iso = datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)), tzinfo=PT_TZ).date()
            return (d_iso - today_pt).days
        except ValueError:
            pass

    # 3. Match DEADLINE/DUE/EXIT/BY MM/DD without year (e.g. "DEADLINE 11/27")
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


def save_history(dog_id: str, shelter: str) -> None:
    hist = load_history()
    if dog_id:
        ids = [x for x in hist["dog_ids"] if x != dog_id]
        ids.append(dog_id)
        hist["dog_ids"] = ids[-60:]
    if shelter:
        s_clean = shelter.strip().lower()
        shelters = hist["shelters"] + [s_clean]
        hist["shelters"] = shelters[-20:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=2)
        f.write("\n")


def select_spotlight_dog() -> dict:
    if not os.path.exists("dogs.json"):
        raise FileNotFoundError("dogs.json not found. Run fetch_dogs.py first.")

    with open("dogs.json", "r", encoding="utf-8") as f:
        dogs = json.load(f)

    if not isinstance(dogs, list) or not dogs:
        raise ValueError("dogs.json is empty.")

    hist = load_history()
    recently_featured = set(hist["dog_ids"])
    recent_shelters = list(hist["shelters"])

    # Backfill last shelter from the most recent dog_id if shelters list is empty
    if not recent_shelters and hist["dog_ids"]:
        last_id = hist["dog_ids"][-1]
        for d in dogs:
            if (d.get("id") or d.get("animal_id")) == last_id:
                s = (d.get("shelter") or "").strip().lower()
                if s:
                    recent_shelters.append(s)
                break

    last_shelter = recent_shelters[-1] if recent_shelters else ""
    last_three_shelters = set(recent_shelters[-3:])

    valid_dogs = []
    day_counts: dict[str, int] = {}
    for d in dogs:
        days_away = parse_deadline_days_away(d)
        key = str(days_away)
        day_counts[key] = day_counts.get(key, 0) + 1
        if days_away is not None and days_away < 0:
            continue
        valid_dogs.append((d, days_away))

    print(f"Total dogs: {len(dogs)} | Parsed days_away: {day_counts} | Recent shelters: {recent_shelters[-3:]}")

    pool = valid_dogs if valid_dogs else [(d, None) for d in dogs]

    def rank_key(item: tuple[dict, int | None]) -> tuple:
        d, days_away = item
        dog_uid = d.get("id") or d.get("animal_id") or ""
        already_used = 1 if dog_uid in recently_featured else 0
        has_photo = 0 if (d.get("photo_url") or "").startswith("http") else 1

        shelter_key = (d.get("shelter") or "").strip().lower()
        if last_shelter and shelter_key == last_shelter:
            shelter_penalty = 2
        elif shelter_key in last_three_shelters:
            shelter_penalty = 1
        else:
            shelter_penalty = 0

        _, is_fallback_name = extract_display_name(d)
        has_real_name = 1 if is_fallback_name else 0

        # Primary target: explicit deadline 2 days from today
        is_exact_two_days = 0 if days_away == 2 else 1

        if days_away == 2:
            urgency_bucket = 0
            day_tiebreak = 0
        elif days_away in (1, 3):
            urgency_bucket = 1
            day_tiebreak = days_away
        elif days_away == 0 or any(
            k in (d.get("deadline_or_urgency") or "").upper()
            for k in ("FINAL CALL", "EUTHANASIA", "OUT OF TIME")
        ):
            urgency_bucket = 2
            day_tiebreak = 0
        else:
            urgency_bucket = 3
            day_tiebreak = days_away if days_away is not None else 999

        return (
            already_used,
            has_photo,
            is_exact_two_days,
            shelter_penalty,
            urgency_bucket,
            has_real_name,
            day_tiebreak,
        )

    pool.sort(key=rank_key)
    chosen = pool[0][0]
    save_history(
        chosen.get("id") or chosen.get("animal_id") or "",
        chosen.get("shelter") or "",
    )
    return chosen


def clean_shelter_label(shelter: str, city: str, aid: str = "") -> str:
    s = re.sub(r"\s+", " ", (shelter or "California Municipal Shelter")).strip()
    s = re.sub(r"\(?\s*Success Story.*$", "", s, flags=re.IGNORECASE).strip()
    if aid:
        s = re.sub(rf"\s*#?\s*{re.escape(aid)}\b", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\(\s*\)", "", s).strip()
    if city and city.lower() not in s.lower() and "(" not in s:
        s = f"{s} ({city}, CA)"
    return s


def build_single_dog_html(dog: dict) -> str:
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
        escaped_name = html.escape(display_name)
        img_html = (
            f'<img class="bg-blur" src="{escaped_photo}" alt="" />'
            f'<div class="fg-frame"><img class="fg-dog" src="{escaped_photo}" alt="{escaped_name}" /></div>'
        )
    else:
        img_html = '<div class="placeholder">Photo on Official Shelter Listing</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=1080, initial-scale=1" />
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{
      width: 1080px;
      height: 1920px;
      overflow: hidden;
      background: #0f172a;
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }}
    .stage {{
      position: relative;
      width: 1080px;
      height: 1920px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .photo-box {{
      position: absolute;
      top: 0;
      left: 0;
      width: 1080px;
      height: 1380px;
      overflow: hidden;
      background: #0f172a;
    }}
    .photo-box img.bg-blur {{
      position: absolute;
      inset: -40px;
      width: calc(100% + 80px);
      height: calc(100% + 80px);
      object-fit: cover;
      filter: blur(28px) brightness(0.45) saturate(0.9);
      transform: scale(1.1);
    }}
    .fg-frame {{
      position: absolute;
      top: 190px;
      left: 0;
      width: 1080px;
      height: 1060px;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .photo-box img.fg-dog {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      object-position: center center;
      transform-origin: center 28%;
      filter: brightness(1.08) contrast(1.07) saturate(1.1) drop-shadow(0 16px 36px rgba(0,0,0,0.65));
      animation: multiCutMotion 8.5s linear forwards;
    }}
    @keyframes multiCutMotion {{
      0%    {{ transform: scale(1.00); }}
      32.9% {{ transform: scale(1.05); }}
      33.0% {{ transform: scale(1.09); }}
      65.9% {{ transform: scale(1.12); }}
      66.0% {{ transform: scale(1.03); }}
      100%  {{ transform: scale(1.06); }}
    }}
    .placeholder {{
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #cbd5e1;
      font-size: 34px;
      font-weight: 600;
    }}
    .top-gradient {{
      position: relative;
      z-index: 10;
      background: linear-gradient(180deg, rgba(15,23,42,0.88) 0%, rgba(15,23,42,0.35) 75%, rgba(15,23,42,0) 100%);
      padding: 120px 48px 40px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .urgent-pill {{
      background: #dc2626;
      color: #ffffff;
      font-size: 28px;
      font-weight: 900;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      padding: 13px 26px;
      border-radius: 999px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.45);
    }}
    .site-pill {{
      background: rgba(15, 23, 42, 0.78);
      border: 2px solid rgba(255,255,255,0.25);
      color: #f8fafc;
      font-size: 26px;
      font-weight: 700;
      padding: 11px 22px;
      border-radius: 999px;
    }}
    .bottom-card {{
      position: relative;
      z-index: 10;
      margin-top: auto;
      background: linear-gradient(180deg, rgba(15,23,42,0) 0%, rgba(15,23,42,0.94) 11%, #0f172a 18%, #0f172a 100%);
      padding: 58px 52px 155px;
    }}
    .name-row {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 20px;
    }}
    .name-row h1 {{
      font-size: 64px;
      font-weight: 900;
      line-height: 1.06;
      letter-spacing: -0.02em;
      color: #ffffff;
    }}
    .aid {{
      font-size: 34px;
      font-weight: 800;
      color: #94a3b8;
      flex-shrink: 0;
    }}
    .shelter {{
      margin-top: 10px;
      font-size: 34px;
      font-weight: 700;
      color: #f8fafc;
    }}
    .meta {{
      margin-top: 8px;
      font-size: 30px;
      color: #cbd5e1;
      font-weight: 500;
    }}
    .needs-box {{
      margin-top: 18px;
      display: inline-block;
      background: rgba(220, 38, 38, 0.22);
      border: 2px solid #ef4444;
      color: #fecaca;
      font-size: 28px;
      font-weight: 800;
      padding: 10px 22px;
      border-radius: 14px;
    }}
    .note {{
      margin-top: 14px;
      font-size: 27px;
      color: #e2e8f0;
      line-height: 1.35;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .cta-bar {{
      margin-top: 24px;
      background: #dc2626;
      color: #ffffff;
      border-radius: 20px;
      padding: 20px 30px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 12px 30px rgba(220, 38, 38, 0.35);
    }}
    .cta-bar span {{
      font-size: 28px;
      font-weight: 700;
    }}
    .cta-bar strong {{
      font-size: 34px;
      font-weight: 900;
    }}
  </style>
</head>
<body>
  <div class="stage">
    <div class="photo-box">
      {img_html}
    </div>
    <header class="top-gradient">
      <span class="urgent-pill">🚨 {html.escape(badge)}</span>
      <span class="site-pill">urgentpaws.org</span>
    </header>
    <section class="bottom-card">
      <div class="name-row">
        <h1>{html.escape(display_name)}</h1>
        {f'<span class="aid">#{html.escape(aid)}</span>' if (aid and not is_fallback_name) else ''}
      </div>
      <p class="shelter">{html.escape(shelter)}</p>
      {f'<p class="meta">{meta}</p>' if meta else ''}
      <div class="needs-box">{html.escape(needs)}</div>
      {f'<p class="note">{html.escape(note)}</p>' if note else ''}
      <div class="cta-bar">
        <span>Official shelter link &amp; live list:</span>
        <strong>urgentpaws.org</strong>
      </div>
    </section>
  </div>
</body>
</html>
"""


def write_caption(dog: dict) -> None:
    aid = (dog.get("animal_id") or "").strip()
    display_name, is_fallback_name = extract_display_name(dog)
    name = display_name if is_fallback_name else (f"{display_name} (#{aid})" if aid else display_name)

    shelter = clean_shelter_label(dog.get("shelter") or "", dog.get("city") or "", aid)
    badge = dog.get("deadline_or_urgency") or "Urgent"
    needs = dog.get("needs_type") or "Rescue, Foster, or Adopter Needed"
    dog_uid = dog.get("id") or aid
    direct_link = f"https://www.urgentpaws.org/?dog={dog_uid}" if dog_uid else "https://www.urgentpaws.org"
    is_laas = "los angeles" in (dog.get("county") or "").lower()

    hashtags = (
        "#LAAnimalServices #UrgentDogsLA #AdoptDontShop #ShelterDogs #FosterSavesLives"
        if is_laas
        else "#UrgentDogs #CaliforniaShelterDogs #AdoptDontShop #ShelterDogs #FosterSavesLives"
    )

    lines = [
        f"🚨 {badge.upper()}: {name}",
        f"📍 Shelter: {shelter}",
        f"🆘 Status: {needs}",
        "",
        "Please share this to your Story or send to someone in California who can foster, adopt, or tag a 501(c)(3) rescue before time runs out.",
        "",
        f"🔗 Direct shelter link & live status:\n{direct_link}",
        "",
        hashtags,
    ]

    with open("reel_caption.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def generate_video(stage_html: str, out_mp4: str = "latest_reel.mp4") -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = os.path.join(tmpdir, "stage.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(stage_html)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1080, "height": 1920},
                device_scale_factor=1,
                record_video_dir=tmpdir,
                record_video_size={"width": 1080, "height": 1920},
            )
            page = context.new_page()
            page.goto(f"file://{html_path}", wait_until="networkidle", timeout=30000)

            page.wait_for_timeout(8500)

            video_path = page.video.path()
            context.close()
            browser.close()

        webm_files = glob.glob(os.path.join(tmpdir, "*.webm"))
        raw_video = video_path if os.path.exists(video_path) else (webm_files[0] if webm_files else "")
        if not raw_video:
            raise RuntimeError("Playwright did not produce a recorded video file.")

        if os.path.exists("audio.mp3"):
            cmd = [
                "ffmpeg", "-y",
                "-i", raw_video,
                "-stream_loop", "-1", "-i", "audio.mp3",
                "-vf", "unsharp=5:5:0.75:5:5:0.0",
                "-shortest",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                out_mp4,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-i", raw_video,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-vf", "unsharp=5:5:0.75:5:5:0.0",
                "-shortest",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                out_mp4,
            ]

        subprocess.run(cmd, check=True)
        print(f"Generated {out_mp4} ({os.path.getsize(out_mp4)} bytes)")


def main() -> None:
    dog = select_spotlight_dog()
    stage_html = build_single_dog_html(dog)
    generate_video(stage_html, out_mp4="latest_reel.mp4")
    write_caption(dog)
    display_name, _ = extract_display_name(dog)
    print(f"Spotlighted {display_name} ({dog.get('animal_id')}) successfully.")


if __name__ == "__main__":
    main()
