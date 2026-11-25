import glob
import html
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright


def load_top_urgent_dogs(limit: int = 5) -> list[dict]:
    if not os.path.exists("dogs.json"):
        raise FileNotFoundError("dogs.json not found. Run fetch_dogs.py first.")

    with open("dogs.json", "r", encoding="utf-8") as f:
        dogs = json.load(f)

    if not isinstance(dogs, list) or not dogs:
        raise ValueError("dogs.json is empty.")

    with_photos = [d for d in dogs if (d.get("photo_url") or "").startswith("http")]
    without_photos = [d for d in dogs if not (d.get("photo_url") or "").startswith("http")]
    return (with_photos + without_photos)[:limit]


def clean_shelter_label(shelter: str, city: str) -> str:
    s = re.sub(r"\s+", " ", (shelter or "California Municipal Shelter")).strip()
    # Strip PetHarbor "Success Story..." boilerplate if captured in location cell
    s = re.sub(r"\(?\s*Success Story.*$", "", s, flags=re.IGNORECASE).strip()
    if city and city.lower() not in s.lower():
        s = f"{s} ({city}, CA)"
    return s


def build_reel_stage_html(dogs: list[dict]) -> str:
    cards = []
    for idx, dog in enumerate(dogs, start=1):
        aid = (dog.get("animal_id") or "").strip()
        raw_name = (dog.get("name") or "").strip()
        # Avoid showing duplicate ID when name is a fallback like "OAS #22731520" or "LAAS A1905992"
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
    .hook-banner {{
      position: sticky;
      top: 0;
      z-index: 50;
      background: linear-gradient(135deg, #b91c1c 0%, #991b1b 100%);
      color: #ffffff;
      padding: 150px 52px 44px;
      box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4);
    }}
    .pill {{
      display: inline-block;
      background: rgba(255, 255, 255, 0.18);
      border: 2px solid rgba(255, 255, 255, 0.35);
      font-size: 26px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      padding: 8px 20px;
      border-radius: 999px;
      margin-bottom: 20px;
    }}
    .hook-banner h1 {{
      font-size: 54px;
      line-height: 1.16;
      font-weight: 800;
      letter-spacing: -0.02em;
    }}
    .hook-banner p {{
      margin-top: 16px;
      font-size: 30px;
      color: #fee2e2;
      font-weight: 500;
    }}
    .feed {{
      padding: 40px 44px 480px;
      display: flex;
      flex-direction: column;
      gap: 44px;
    }}
    .card {{
      background: #ffffff;
      border-radius: 36px;
      overflow: hidden;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.32);
    }}
    .img-wrap {{
      position: relative;
      width: 100%;
      height: 760px;
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
      font-size: 28px;
      font-weight: 800;
      padding: 12px 26px;
      border-radius: 999px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .body {{
      padding: 36px 40px 40px;
    }}
    .row-top {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 20px;
    }}
    .row-top h2 {{
      font-size: 50px;
      font-weight: 800;
      color: #0f172a;
    }}
    .aid {{
      font-size: 32px;
      font-weight: 700;
      color: #475569;
    }}
    .shelter {{
      margin-top: 10px;
      font-size: 32px;
      font-weight: 700;
      color: #1e293b;
    }}
    .meta {{
      margin-top: 12px;
      font-size: 30px;
      color: #475569;
      font-weight: 500;
    }}
    .needs {{
      margin-top: 20px;
      display: inline-block;
      background: #fef2f2;
      color: #991b1b;
      font-size: 28px;
      font-weight: 700;
      padding: 10px 20px;
      border-radius: 16px;
    }}
    .note {{
      margin-top: 16px;
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
  <header class="hook-banner">
    <span class="pill">Live California Shelter Alert</span>
    <h1>California shelter dogs facing imminent deadlines</h1>
    <p>Auto-synced every 3 hours from 8 municipal shelters</p>
  </header>
  <main class="feed">
    {''.join(cards)}
    <section class="cta-card">
      <h3>View Full Live List &amp; Official Shelter Links</h3>
      <p>Filter by Bay Area, Central Valley, or SoCal shelter</p>
      <div class="cta-url">urgentpaws.org</div>
    </section>
  </main>
</body>
</html>
"""


def write_caption(dogs: list[dict]) -> None:
    today_str = datetime.now(timezone.utc).strftime("%b %d, %Y")
    lines = [
        f"🚨 URGENT CALIFORNIA SHELTER DOGS ({today_str})",
        "",
        "These dogs are currently on municipal urgent / deadline boards and need immediate adopters, fosters, or 501(c)(3) rescue holds:",
        "",
    ]
    for d in dogs:
        aid = (d.get("animal_id") or "").strip()
        raw_name = (d.get("name") or "").strip()
        name = f"Urgent Dog #{aid}" if (not raw_name or (aid and aid.lower() in raw_name.lower())) else f"{raw_name} (#{aid})"
        shelter = clean_shelter_label(d.get("shelter") or "", d.get("city") or "")
        badge = d.get("deadline_or_urgency") or "Urgent"
        lines.append(f"• {name} — {shelter} [{badge}]")

    lines.extend(
        [
            "",
            "🔗 View live status & direct shelter contact links at: https://www.urgentpaws.org",
            "",
            "#UrgentDogs #CaliforniaShelterDogs #AdoptDontShop #ShelterDogs #FosterSavesLives #RescueDogsOfInstagram #BayAreaDogs #SoCalDogs",
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

            page.wait_for_timeout(2500)

            total_height = page.evaluate("document.body.scrollHeight - window.innerHeight")
            steps = 220
            step_px = max(total_height / steps, 1)
            for _ in range(steps):
                page.evaluate(f"window.scrollBy(0, {step_px})")
                page.wait_for_timeout(50)

            page.wait_for_timeout(2000)

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
    dogs = load_top_urgent_dogs(limit=5)
    stage_html = build_reel_stage_html(dogs)
    generate_video(stage_html, out_mp4="latest_reel.mp4")
    write_caption(dogs)
    print("Generated reel_caption.txt successfully.")


if __name__ == "__main__":
    main()
