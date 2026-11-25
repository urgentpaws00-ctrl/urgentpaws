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


def is_expired_deadline(dog: dict) -> bool:
    badge = dog.get("deadline_or_urgency") or ""
    m = re.search(r"\bDEADLINE\s+(\d{1,2})/(\d{1,2})/(\d{2,4})\b", badge, re.IGNORECASE)
    if not m:
        return False
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        deadline_date = datetime(year, month, day, tzinfo=PT_TZ).date()
        today_pt = datetime.now(PT_TZ).date()
        return deadline_date < today_pt
    except ValueError:
        return False


def load_top_urgent_dogs(limit: int = 3) -> list[dict]:
    if not os.path.exists("dogs.json"):
        raise FileNotFoundError("dogs.json not found. Run fetch_dogs.py first.")

    with open("dogs.json", "r", encoding="utf-8") as f:
        dogs = json.load(f)

    if not isinstance(dogs, list) or not dogs:
        raise ValueError("dogs.json is empty.")

    active_dogs = [d for d in dogs if not is_expired_deadline(d)]
    pool = active_dogs if active_dogs else dogs

    with_photos = [d for d in pool if (d.get("photo_url") or "").startswith("http")]
    without_photos = [d for d in pool if not (d.get("photo_url") or "").startswith("http")]
    return (with_photos + without_photos)[:limit]


def clean_shelter_label(shelter: str, city: str) -> str:
    s = re.sub(r"\s+", " ", (shelter or "California Municipal Shelter")).strip()
    s = re.sub(r"\(?\s*Success Story.*$", "", s, flags=re.IGNORECASE).strip()
    if city and city.lower() not in s.lower():
        s = f"{s} ({city}, CA)"
    return s


def build_reel_stage_html(dogs: list[dict]) -> str:
    cards = []
    for idx, dog in enumerate(dogs, start=1):
        aid = (dog.get("animal_id") or "").strip()
        raw_name = (dog.get("name") or "").strip()
        is_fallback_name = not raw_name or (aid and aid.lower() in raw_name.lower())
        display_name = f"Urgent Dog #{aid}" if is_fallback_name else raw_name

        shelter = clean_shelter_label(dog.get("shelter") or "", dog.get("city") or "")
        badge = dog.get("deadline_or_urgency") or "URGENT"
        needs = dog.get("needs_type") or "Rescue / Foster / Adopt"
        note = (dog.get("transport_note") or "").strip()
        photo = (dog.get("photo_url") or "").strip()
        meta = " • ".join(
            html.escape(x)
            for x in [dog.get("breed", ""), dog.get("age", ""), dog.get("size", "")]
            if x
        )

        img_html = (
            f'<img src="{html.escape(photo)}" alt="{html.escape(display_name)}" onerror="this.style.display=\'none\'" />'
            if photo
            else '<div class="placeholder">Photo on Official Shelter Listing</div>'
        )

        cards.append(
            f"""
            <article class="card" id="card-{idx}">
              <div class="img-wrap">
                {img_html}
                <span class="badge">{html.escape(badge)}</span>
                <span class="counter">{idx} / {len(dogs)}</span>
              </div>
              <div class="body">
                <div class="row-top">
                  <h2>{html.escape(display_name)}</h2>
                  {f'<span class="aid">#{html.escape(aid)}</span>' if (aid and not is_fallback_name) else ''}
                </div>
                <p class="shelter">{html.escape(shelter)}</p>
                {f'<p class="meta">{meta}</p>' if meta else ''}
                <p class="needs">{html.escape(needs)}</p>
                {f'<p class="note">{html.escape(note)}</p>' if note else ''}
              </div>
            </article>
            """
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
      background: #0f172a;
      color: #0f172a;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      overflow-x: hidden;
    }}
    /* Compact top bar clear of IG's top UI overlay so Dog #1 fills Frame 1 */
    .top-bar {{
      padding: 155px 44px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .live-pill {{
      background: #dc2626;
      color: #ffffff;
      font-size: 26px;
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      padding: 10px 22px;
      border-radius: 999px;
    }}
    .brand-tag {{
      color: #e2e8f0;
      font-size: 28px;
      font-weight: 700;
    }}
    .feed {{
      padding: 10px 44px 420px;
      display: flex;
      flex-direction: column;
      gap: 44px;
    }}
    .card {{
      background: #ffffff;
      border-radius: 36px;
      overflow: hidden;
      box-shadow: 0 24px 56px rgba(0, 0, 0, 0.4);
    }}
    .img-wrap {{
      position: relative;
      width: 100%;
      height: 920px;
      background: #1e293b;
    }}
    .img-wrap img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      object-position: center 20%;
      display: block;
    }}
    .placeholder {{
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #cbd5e1;
      font-size: 32px;
      font-weight: 600;
    }}
    .badge {{
      position: absolute;
      top: 28px;
      left: 28px;
      background: #dc2626;
      color: #ffffff;
      font-size: 30px;
      font-weight: 800;
      padding: 14px 28px;
      border-radius: 999px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .counter {{
      position: absolute;
      top: 28px;
      right: 28px;
      background: rgba(15, 23, 42, 0.78);
      color: #ffffff;
      font-size: 26px;
      font-weight: 700;
      padding: 12px 22px;
      border-radius: 999px;
    }}
    .body {{
      padding: 34px 40px 38px;
    }}
    .row-top {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 20px;
    }}
    .row-top h2 {{
      font-size: 52px;
      font-weight: 800;
      color: #0f172a;
    }}
    .aid {{
      font-size: 32px;
      font-weight: 700;
      color: #475569;
    }}
    .shelter {{
      margin-top: 8px;
      font-size: 34px;
      font-weight: 700;
      color: #1e293b;
    }}
    .meta {{
      margin-top: 10px;
      font-size: 30px;
      color: #475569;
      font-weight: 500;
    }}
    .needs {{
      margin-top: 18px;
      display: inline-block;
      background: #fef2f2;
      color: #991b1b;
      font-size: 28px;
      font-weight: 700;
      padding: 10px 20px;
      border-radius: 16px;
    }}
    .note {{
      margin-top: 14px;
      font-size: 28px;
      color: #334155;
      line-height: 1.4;
    }}
    .cta-card {{
      background: #1e293b;
      color: #ffffff;
      border: 4px solid #334155;
      border-radius: 36px;
      padding: 52px 44px;
      text-align: center;
    }}
    .cta-card h3 {{
      font-size: 46px;
      font-weight: 800;
    }}
    .cta-card p {{
      margin-top: 16px;
      font-size: 32px;
      color: #cbd5e1;
    }}
    .cta-url {{
      margin-top: 28px;
      display: inline-block;
      background: #dc2626;
      color: #ffffff;
      font-size: 38px;
      font-weight: 800;
      padding: 20px 44px;
      border-radius: 20px;
    }}
  </style>
</head>
<body>
  <header class="top-bar">
    <span class="live-pill">🚨 CA Shelter Deadlines</span>
    <span class="brand-tag">urgentpaws.org</span>
  </header>
  <main class="feed">
    {''.join(cards)}
    <section class="cta-card">
      <h3>View Full Live List &amp; Official Shelter Links</h3>
      <p>Updated every 3 hours across 8 CA shelters</p>
      <div class="cta-url">urgentpaws.org</div>
    </section>
  </main>
</body>
</html>
"""


def write_caption(dogs: list[dict]) -> None:
    today_str = datetime.now(PT_TZ).strftime("%b %d, %Y")
    has_laas = any("los angeles" in (d.get("county") or "").lower() for d in dogs)

    lines = [
        f"🚨 CA Shelter Dogs Facing Imminent Deadlines ({today_str})",
        "",
        "Share this to your Story or send to someone in California who can foster, adopt, or tag a 501(c)(3) rescue before time runs out:",
        "",
    ]
    for d in dogs:
        aid = (d.get("animal_id") or "").strip()
        raw_name = (d.get("name") or "").strip()
        name = (
            f"Urgent Dog #{aid}"
            if (not raw_name or (aid and aid.lower() in raw_name.lower()))
            else f"{raw_name} (#{aid})"
        )
        shelter = clean_shelter_label(d.get("shelter") or "", d.get("city") or "")
        badge = d.get("deadline_or_urgency") or "Urgent"
        lines.append(f"• {name} — {shelter} [{badge}]")

    hashtags = (
        "#LAAnimalServices #UrgentDogsLA #AdoptDontShop #ShelterDogs #FosterSavesLives"
        if has_laas
        else "#UrgentDogs #CaliforniaShelterDogs #AdoptDontShop #ShelterDogs #FosterSavesLives"
    )

    lines.extend(
        [
            "",
            "🔗 Live status & official shelter links in bio → urgentpaws.org",
            "",
            hashtags,
        ]
    )

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

            # Hold 1.8s on Dog #1 (photo & deadline badge fill the center of Frame 1)
            page.wait_for_timeout(1800)

            # Smooth scroll through Dogs #2 and #3 over ~6.2s
            total_height = page.evaluate("document.body.scrollHeight - window.innerHeight")
            steps = 155
            step_px = max(total_height / steps, 1)
            for _ in range(steps):
                page.evaluate(f"window.scrollBy(0, {step_px})")
                page.wait_for_timeout(40)

            # Hold 1.5s on the urgentpaws.org CTA card
            page.wait_for_timeout(1500)

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
    dogs = load_top_urgent_dogs(limit=3)
    stage_html = build_reel_stage_html(dogs)
    generate_video(stage_html, out_mp4="latest_reel.mp4")
    write_caption(dogs)
    print("Generated reel_caption.txt successfully.")


if __name__ == "__main__":
    main()
