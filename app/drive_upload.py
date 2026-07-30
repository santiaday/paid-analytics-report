"""Upload the self-contained Paid-Analytics.html to a Google Drive folder.

Reuses the existing Google OAuth (same client as Google Ads + a Drive-scoped refresh token — the
repo's Sheets adapter uses `drive.file`). No service account. On the first run it CREATES the file in
the target folder; thereafter it UPDATES that same file in place, so the file + its shared link stay
stable. Only the standard library + `requests`.

Env:
  GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET   shared OAuth client (already set for the Google Ads pull)
  GDRIVE_REFRESH_TOKEN                              refresh token with drive.file scope
                                                   (the Sheets adapter's GOOGLE_SHEETS_REFRESH_TOKEN works)
  GDRIVE_FOLDER_ID                                  the target Drive folder (e.g. the "All Channels" folder)
  GDRIVE_FILE_NAME                                  optional; default "Paid-Analytics.html"
"""
from __future__ import annotations

import json
import os
from typing import Optional

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_V3 = "https://www.googleapis.com/drive/v3"
UPLOAD_V3 = "https://www.googleapis.com/upload/drive/v3"


def _access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    r = requests.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    }, timeout=30)
    if not r.ok:
        raise RuntimeError(f"google token refresh {r.status_code}: {r.text[:300]}")
    return r.json()["access_token"]


def _find_existing(token: str, folder_id: str, name: str) -> Optional[str]:
    """The id of an existing <name> in <folder_id> (app-created), or None."""
    q = f"name = '{name}' and '{folder_id}' in parents and trashed = false"
    r = requests.get(f"{DRIVE_V3}/files", params={
        "q": q, "fields": "files(id,name)", "spaces": "drive",
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
    }, headers={"authorization": f"Bearer {token}"}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"drive files.list {r.status_code}: {r.text[:300]}")
    files = r.json().get("files", [])
    return files[0]["id"] if files else None


def download(folder_id: str, name: str, dest_path: str, refresh: Optional[str] = None) -> bool:
    """Download <name> from <folder_id> into dest_path. Returns False if the file isn't there yet."""
    client_id = os.environ["GOOGLE_ADS_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_ADS_CLIENT_SECRET"]
    refresh = refresh or os.environ.get("GDRIVE_REFRESH_TOKEN") or os.environ.get("GOOGLE_SHEETS_REFRESH_TOKEN")
    if not refresh:
        raise RuntimeError("no GDRIVE_REFRESH_TOKEN (or GOOGLE_SHEETS_REFRESH_TOKEN)")
    token = _access_token(client_id, client_secret, refresh)
    fid = _find_existing(token, folder_id, name)
    if not fid:
        return False
    r = requests.get(f"{DRIVE_V3}/files/{fid}", params={"alt": "media", "supportsAllDrives": "true"},
                     headers={"authorization": f"Bearer {token}"}, timeout=600, stream=True)
    if not r.ok:
        raise RuntimeError(f"drive download {r.status_code}: {r.text[:300]}")
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(65536):
            f.write(chunk)
    print(f"[drive] downloaded {name} → {dest_path} ({os.path.getsize(dest_path)} bytes)", flush=True)
    return True


def upload(html_path: str, folder_id: Optional[str] = None, name: Optional[str] = None) -> str:
    """Create-or-update the report file in Drive. Returns the file id. Resumable upload (handles >5MB)."""
    client_id = os.environ["GOOGLE_ADS_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_ADS_CLIENT_SECRET"]
    refresh = os.environ.get("GDRIVE_REFRESH_TOKEN") or os.environ.get("GOOGLE_SHEETS_REFRESH_TOKEN")
    if not refresh:
        raise RuntimeError("no GDRIVE_REFRESH_TOKEN (or GOOGLE_SHEETS_REFRESH_TOKEN)")
    folder_id = folder_id or os.environ["GDRIVE_FOLDER_ID"]
    name = name or os.environ.get("GDRIVE_FILE_NAME", "Paid-Analytics.html")

    token = _access_token(client_id, client_secret, refresh)
    with open(html_path, "rb") as f:
        content = f.read()

    existing = _find_existing(token, folder_id, name)
    hdr = {"authorization": f"Bearer {token}", "content-type": "application/json; charset=UTF-8"}
    if existing:
        # Resumable UPDATE — overwrite the same file's content (keeps id + link stable).
        init = requests.patch(f"{UPLOAD_V3}/files/{existing}?uploadType=resumable&supportsAllDrives=true",
                              headers=hdr, data=json.dumps({"name": name}), timeout=30)
    else:
        meta = {"name": name, "parents": [folder_id], "mimeType": "text/html"}
        init = requests.post(f"{UPLOAD_V3}/files?uploadType=resumable&supportsAllDrives=true",
                             headers=hdr, data=json.dumps(meta), timeout=30)
    if not init.ok:
        raise RuntimeError(f"drive resumable init {init.status_code}: {init.text[:300]}")
    session_url = init.headers["location"]

    put = requests.put(session_url, headers={"content-type": "text/html; charset=UTF-8"},
                       data=content, timeout=600)
    if not put.ok:
        raise RuntimeError(f"drive upload {put.status_code}: {put.text[:300]}")
    file_id = put.json().get("id", existing)
    print(f"[drive] {'updated' if existing else 'created'} {name} ({len(content)} bytes) → file {file_id}", flush=True)
    return file_id
