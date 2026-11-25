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


def parse_deadline_days_away(dog: dict) -> int | None:
    """Returns days from today (PT) until deadline, or None if no calendar date."""
    badge = dog.get("deadline_or_urgency") or ""
    m = re.search(r"\bDEADLINE\s+(\d{1,2})/(\d{1,2})/(\d{2,4})\b", badge, re.IGNORECASE)
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        deadline_date = datetime(year, month, day, tzinfo=PT_TZ).date()
        today_pt = datetime.now(PT_TZ).date()
        return (deadline_date - today_pt).days
    except ValueError:
        return None


def load_featured_ids() -> list[str]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_featured_id(dog_id: str) -> None:
    if not dog_id:
        return
    history = [x for x in load_featured_ids() if x != dog_id]
    history.append(dog_id)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-30:], f, indent=2)
        f.write("\n")


def select_spotlight_dog() -> dict:
    if not os.path.exists("dogs.json"):
        raise FileNotFoundError("dogs.json not found. Run fetch_dogs.py first.")

    with open("dogs.json", "r", encoding="utf-8") as f:
        dogs = json.load(f)

    if not isinstance(dogs, list) or not dogs:
        raise ValueError("dogs.json is empty.")

    recently_featured = set(load_featured_ids())
    valid_dogs = []
    for d in dogs:
        days_away = parse_deadline_days_away(d)
        if days_away is not None and days_away < 0:
            continue
        valid_dogs.append((d, days_away))

    pool = valid_dogs if valid_dogs else [(d, None) for d in dogs]

    def rank_key(item: tuple[dict, int | None]) -> tuple:
        d, days_away = item
        dog_uid = d.get("id") or d.get("animal_id") or ""
        already_used = 1 if dog_uid in recently_featured else 0
        has_photo = 0 if (d.get("photo_url") or "").startswith("http") else 1

        aid = (d.get("animal_id") or "").strip()
        raw_name = (d.get("name") or "").strip()
        has_real_name = 0 if (raw_name and (not aid or aid.lower() not in raw_name.lower())) else 1

        # 0 = Explicit deadline 2 days from today (primary target)
        # 1 = Explicit deadline 1 or 3 days from today (fallback runway)
        # 2 = Explicit deadline today (0 days away)
        # 3 = Imminent alert without date (FINAL CALL / EUTHANASIA ALERT / OUT OF TIME)
        # 4 = Explicit deadline 4+ days away or general urgent listing
        if days_away == 2:
            urgency_bucket = 0
            day_tiebreak = 0
        elif days_away in (1, 3):
            urgency_bucket = 1
            day_tiebreak = days_away
        elif days_away == 0:
            urgency_bucket = 2
            day_tiebreak = 0
        elif any(k in (d.get("deadline_or_urgency") or "").upper() for k in ("FINAL CALL", "EUTHANASIA", "OUT OF TIME")):
            urgency_bucket = 3
            day_tiebreak = 99
        else:
            urgency_bucket = 4
            day_tiebreak = days_away if days_away is not None else 999

        return (already_used, has_photo, urgency_bucket, has_real_name, day_tiebreak)

    pool.sort(key=rank_key)
    chosen = pool[0][0]
    save_featured_id(chosen.get("id") or chosen.get("animal_id") or "")
    return chosen


def clean_shelter_label(shelter: str, city: str) -> str:
    s = re.sub(r"\s+", " ", (shelter or "California Municipal Shelter")).strip()
    s = re.sub(r"\(?\s*Success Story.*$", "", s, flags=re.IGNORECASE).strip()
    if city and city.lower() not in s.lower():
        s = f"{s} ({city}, CA)"
    return s


def build_single_dog_html(dog: dict) -> str:
    aid = (dog.get("animal_id") or "").strip()
    raw_name = (dog.get("name") or "").strip()
    is_fallback_name = not raw_name or (aid and aid.lower() in raw_name.lower())
    display_name = f"Urgent Dog #{aid}" if is_fallback_name else raw_name

    shelter = clean_shelter_label(dog.get("shelter") or "", dog.get("city") or "")
    badge = dog.get("deadline_or_urgency") or "URGENT DEADLINE"
    needs = dog.get("needs_type") or "Needs Immediate Rescue, Foster, or Adopter"
    note = (dog.get("transport_note") or "").strip()
    photo = (dog.get("photo_url") or "").strip()
    meta = " • ".join(
        html.escape(x)
        for x in [dog.get("breed", ""), dog.get("age", ""), dog.get("size", "")]
        if x
    )

    img_html = (
        f'<img src="{html.escape(photo)}" alt="{html.escape(display_name)}" />'
        if photo
        else '<div class="placeholder">Photo on Official Shelter Listing</div>'
    )

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
      inset: 0;
      width: 1080px;
      height: 1340px;
      overflow: hidden;
      background: #1e293b;
    }}
    /* 3-cut visual pattern interrupt over 8.5s:
       0-2.8s tight face crop -> 2.8-5.6s full portrait -> 5.6-8.5s medium zoom */
    .photo-box img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      object-position: center 20%;
      animation: multiCutMotion 8.5s linear forwards;
    }}
    @keyframes multiCutMotion {{
      0%    {{ transform: scale(1.18) translateY(2%); }}
      32.9% {{ transform: scale(1.22) translateY(2%); }}
      33.0% {{ transform: scale(1.00) translateY(0%); }}
      65.9% {{ transform: scale(1.04) translateY(0%); }}
      66.0% {{ transform: scale(1.10) translateY(-1%); }}
      100%  {{ transform: scale(1.14) translateY(-1%); }}
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
      background: linear-gradient(180deg, rgba(15,23,42,0.88) 0%, rgba(15,23,42,0.42) 70%, rgba(15,23,42,0) 100%);
      padding: 155px 48px 60px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .urgent-pill {{
      background: #dc2626;
      color: #ffffff;
      font-size: 30px;
      font-weight: 900;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      padding: 14px 28px;
      border-radius: 999px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.45);
    }}
    .site-pill {{
      background: rgba(15, 23, 42, 0.78);
      border: 2px solid rgba(255,255,255,0.25);
      color: #f8fafc;
      font-size: 28px;
      font-weight: 700;
      padding: 12px 24px;
      border-radius: 999px;
    }}
    .bottom-card {{
      position: relative;
      z-index: 10;
      margin-top: auto;
      background: linear-gradient(180deg, rgba(15,23,42,0) 0%, #0f172a 14%, #0f172a 100%);
      padding: 95px 52px 175px;
    }}
    .name-row {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 20px;
    }}
    .name-row h1 {{
      font-size: 68px;
      font-weight: 900;
      line-height: 1.08;
      letter-spacing: -0.02em;
      color: #ffffff;
    }}
    .aid {{
      font-size: 36px;
      font-weight: 800;
      color: #94a3b8;
      flex-shrink: 0;
    }}
    .shelter {{
      margin-top: 14px;
      font-size: 36px;
      font-weight: 700;
      color: #f8fafc;
    }}
    .meta {{
      margin-top: 12px;
      font-size: 32px;
      color: #cbd5e1;
      font-weight: 500;
    }}
    .needs-box {{
      margin-top: 26px;
      display: inline-block;
      background: rgba(220, 38, 38, 0.22);
      border: 2px solid #ef4444;
      color: #fecaca;
      font-size: 30px;
      font-weight: 800;
      padding: 12px 24px;
      border-radius: 16px;
    }}
    .note {{
      margin-top: 18px;
      font-size: 29px;
      color: #e2e8f0;
      line-height: 1.4;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .cta-bar {{
      margin-top: 34px;
      background: #dc2626;
      color: #ffffff;
      border-radius: 22px;
      padding: 24px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 12px 30px rgba(220, 38, 38, 0.35);
    }}
    .cta-bar span {{
      font-size: 30px;
      font-weight: 700;
    }}
    .cta-bar strong {{
      font-size: 36px;
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
    raw_name = (dog.get("name") or "").strip()
    name = (
        f"Urgent Dog #{aid}"
        if (not raw_name or (aid and aid.lower() in raw_name.lower()))
        else f"{raw_name} (#{aid})"
    )
    shelter = clean_shelter_label(dog.get("shelter") or "", dog.get("city") or "")
    badge = dog.get("deadline_or_urgency") or "Urgent"
    needs = dog.get("needs_type") or "Rescue, Foster, or Adopter Needed"
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
        "🔗 Direct shelter link & live status in bio → urgentpaws.org",
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

            # Hold 8.5 seconds on the single spotlighted dog while 3-cut zoom runs
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
    print(f"Spotlighted {dog.get('name')} ({dog.get('animal_id')}) successfully.")


if __name__ == "__main__":
    main()
