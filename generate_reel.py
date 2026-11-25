import glob
import html
import json
import os
import shutil
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

    # Prefer dogs that have photos so the Reel is visually compelling
    with_photos = [d for d in dogs if (d.get("photo_url") or "").startswith("http")]
    without_photos = [d for d in dogs if not (d.get("photo_url") or "").startswith("http")]
    ordered = with_photos + without_photos
    return ordered[:limit]


def build_reel_stage_html(dogs: list[dict]) -> str:
    cards = []
    for idx, dog in enumerate(dogs, start=1):
        name = html.escape(dog.get("name") or f"Dog {dog.get('animal_id', '')}")
        aid = html.escape(dog.get("animal_id") or "")
        shelter = html.escape(dog.get("shelter") or "California Municipal Shelter")
        city = html.escape(dog.get("city") or "")
        badge = html.escape(dog.get("deadline_or_urgency") or "URGENT")
        needs = html.escape(dog.get("needs_type") or "Rescue / Foster / Adopt")
        note = html.escape(dog.get("transport_note") or "")
        photo = html.escape(dog.get("photo_url") or "")
        meta = " • ".join(
            html.escape(x)
            for x in [dog.get("breed", ""), dog.get("age", ""), dog.get("size", "")]
            if x
        )

        img_html = (
            f'<img src="{photo}" alt="{name}" onerror="this.style.display=\'none\'" />'
            if photo
            else '<div class="placeholder">Photo on Official Shelter Listing</div>'
        )

        cards.append(
            f"""
            <article class="card" id="card-{idx}">
              <div class="img-wrap">
                {img_html}
                <span class="badge">{badge}</span>
              </div>
              <div class="body">
                <div class="row-top">
                  <h2>{name}</h2>
                  <span class="aid">#{aid}</span>
                </div>
                <p class="shelter">{shelter}{f' ({city}, CA)' if city else ''}</p>
                {f'<p class="meta">{meta}</p>' if meta else ''}
                <p class="needs">{needs}</p>
                {f'<p class="note">{note}</p>' if note else ''}
              </div>
            </article>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=540, initial-scale=1" />
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      width: 540px;
      background: #0f172a;
      color: #0f172a;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      overflow-x: hidden;
    }}
    /* Keep hook clear of Instagram/TikTok top UI safe zone */
    .hook-banner {{
      position: sticky;
      top: 0;
      z-index: 50;
      background: linear-gradient(135deg, #b91c1c 0%, #991b1b 100%);
      color: #ffffff;
      padding: 74px 26px 22px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    }}
    .pill {{
      display: inline-block;
      background: rgba(255, 255, 255, 0.18);
      border: 1px solid rgba(255, 255, 255, 0.35);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      padding: 4px 10px;
      border-radius: 999px;
      margin-bottom: 10px;
    }}
    .hook-banner h1 {{
      font-size: 27px;
      line-height: 1.18;
      font-weight: 800;
      letter-spacing: -0.02em;
    }}
    .hook-banner p {{
      margin-top: 8px;
      font-size: 15px;
      color: #fee2e2;
      font-weight: 500;
    }}
    .feed {{
      padding: 20px 22px 240px;
      display: flex;
      flex-direction: column;
      gap: 22px;
    }}
    .card {{
      background: #ffffff;
      border-radius: 18px;
      overflow: hidden;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.28);
    }}
    .img-wrap {{
      position: relative;
      width: 100%;
      height: 360px;
      background: #1e293b;
    }}
    .img-wrap img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .placeholder {{
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #cbd5e1;
      font-size: 16px;
      font-weight: 600;
    }}
    .badge {{
      position: absolute;
      top: 14px;
      left: 14px;
      background: #dc2626;
      color: #ffffff;
      font-size: 14px;
      font-weight: 800;
      padding: 6px 13px;
      border-radius: 999px;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .body {{
      padding: 18px 20px 20px;
    }}
    .row-top {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
    }}
    .row-top h2 {{
      font-size: 25px;
      font-weight: 800;
      color: #0f172a;
    }}
    .aid {{
      font-size: 16px;
      font-weight: 700;
      color: #475569;
    }}
    .shelter {{
      margin-top: 4px;
      font-size: 16px;
      font-weight: 700;
      color: #1e293b;
    }}
    .meta {{
      margin-top: 6px;
      font-size: 15px;
      color: #475569;
      font-weight: 500;
    }}
    .needs {{
      margin-top: 10px;
      display: inline-block;
      background: #fef2f2;
      color: #991b1b;
      font-size: 14px;
      font-weight: 700;
      padding: 5px 10px;
      border-radius: 8px;
    }}
    .note {{
      margin-top: 8px;
      font-size: 14px;
      color: #334155;
      line-height: 1.4;
    }}
    .cta-card {{
      background: #1e293b;
      color: #ffffff;
      border: 2px solid #334155;
      border-radius: 18px;
      padding: 26px 22px;
      text-align: center;
    }}
    .cta-card h3 {{
      font-size: 23px;
      font-weight: 800;
    }}
    .cta-card p {{
      margin-top: 8px;
      font-size: 16px;
      color: #cbd5e1;
    }}
    .cta-url {{
      margin-top: 14px;
      display: inline-block;
      background: #dc2626;
      color: #ffffff;
      font-size: 18px;
      font-weight: 800;
      padding: 10px 20px;
      border-radius: 10px;
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
        name = d.get("name") or "Urgent Dog"
        aid = d.get("animal_id") or ""
        shelter = d.get("shelter") or ""
        badge = d.get("deadline_or_urgency") or "Urgent"
        lines.append(f"• {name} (#{aid}) — {shelter} [{badge}]")

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
                viewport={"width": 540, "height": 960},
                device_scale_factor=2,
                record_video_dir=tmpdir,
                record_video_size={"width": 1080, "height": 1920},
            )
            page = context.new_page()
            page.goto(f"file://{html_path}", wait_until="networkidle", timeout=30000)

            # Hold 2.5s on the #1 highest-urgency dog so viewers read the hook & first card
            page.wait_for_timeout(2500)

            # Smoothly scroll down through the remaining cards over ~11 seconds
            total_height = page.evaluate("document.body.scrollHeight - window.innerHeight")
            steps = 220
            step_px = max(total_height / steps, 1)
            for _ in range(steps):
                page.evaluate(f"window.scrollBy(0, {step_px})")
                page.wait_for_timeout(50)

            # Hold 2s on the urgentpaws.org CTA card at the bottom
            page.wait_for_timeout(2000)

            video_path = page.video.path()
            context.close()
            browser.close()

        webm_files = glob.glob(os.path.join(tmpdir, "*.webm"))
        raw_video = video_path if os.path.exists(video_path) else (webm_files[0] if webm_files else "")
        if not raw_video:
            raise RuntimeError("Playwright did not produce a recorded video file.")

        # Mix in audio.mp3 if present in repo root; otherwise add a silent AAC track
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
