"""Small, observable HTTP service used for a first local DeployForge walkthrough."""

import html
import os
import socket
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

SERVICE_NAME = os.getenv("SERVICE_NAME", "DeployForge Demo Service")
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
RELEASE_SHA = os.getenv("RELEASE_SHA", "local-development")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", socket.gethostname())

app = FastAPI(
    title=SERVICE_NAME,
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> HTMLResponse:
    service = html.escape(SERVICE_NAME)
    version = html.escape(APP_VERSION)
    release = html.escape(RELEASE_SHA)
    instance = html.escape(INSTANCE_NAME)
    checked_at = html.escape(datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"))
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{service} · DeployForge demo</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
      padding: 24px; color: #e8eef8; background: radial-gradient(ellipse at 20% 0%, #102b3c, #080d15 58%); }}
    main {{ width: min(100%, 700px); border: 1px solid #25354a; border-radius: 20px;
      padding: clamp(24px, 6vw, 48px); background: #0c1420eF; box-shadow: 0 28px 90px #0008; }}
    .brand {{ color: #61d7df; font-size: 12px; font-weight: 750; letter-spacing: .16em; text-transform: uppercase; }}
    h1 {{ margin: 22px 0 10px; font-size: clamp(30px, 6vw, 48px); letter-spacing: -.04em; }}
    p {{ color: #9aabc1; line-height: 1.7; }}
    .healthy {{ display: inline-flex; align-items: center; gap: 9px; margin: 18px 0 28px;
      padding: 9px 13px; border: 1px solid #1f5549; border-radius: 999px; color: #8ce5bc; background: #10281f; }}
    .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #57dc9b; box-shadow: 0 0 12px #57dc9b; }}
    dl {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 0; }}
    dl div {{ min-width: 0; padding: 15px; border: 1px solid #202e40; border-radius: 12px; background: #101b29; }}
    dt {{ margin-bottom: 8px; color: #7f91a9; font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }}
    dd {{ overflow-wrap: anywhere; margin: 0; color: #dce6f3; font: 13px ui-monospace, SFMono-Regular, monospace; }}
    footer {{ margin-top: 28px; color: #74859b; font-size: 12px; }}
  </style>
</head>
<body>
  <main>
    <div class="brand">DeployForge · Local walkthrough</div>
    <h1>{service}</h1>
    <p>This small service is running locally. Its health endpoints and release metadata are ready for the next platform walkthrough.</p>
    <div class="healthy"><span class="dot"></span>Service is healthy</div>
    <dl>
      <div><dt>Version</dt><dd>{version}</dd></div>
      <div><dt>Release</dt><dd>{release}</dd></div>
      <div><dt>Instance</dt><dd>{instance}</dd></div>
      <div><dt>Checked at</dt><dd>{checked_at}</dd></div>
    </dl>
    <footer>Try <code>/health/live</code>, <code>/health/ready</code> or <code>/api/info</code>.</footer>
  </main>
</body>
</html>"""
    )


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/health/ready", tags=["health"])
def readiness() -> dict[str, str]:
    return {"status": "ready", "service": SERVICE_NAME}


@app.get("/api/info", tags=["service"])
def service_info() -> dict[str, str]:
    return {
        "service": SERVICE_NAME,
        "version": APP_VERSION,
        "release": RELEASE_SHA,
        "instance": INSTANCE_NAME,
    }
