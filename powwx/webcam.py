"""Webcam grabber: fetch the two Shames frames and push them to Cloudflare R2.

The public URLs only ever hold the *latest* frame, so history exists only if we
capture now. Frames go to object storage (never git). Cameras run seasonally and
go dark off-season; a missing/non-image response is skipped, not an error.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import requests

REQUEST_TIMEOUT = 30

CAMERAS = {
    "camera1": "https://storage.googleapis.com/shames_webcam_images/camera1-latest-timestamp.png",
    "camera2": "https://storage.googleapis.com/shames_webcam_images/camera2-latest-timestamp.png",
}


def frame_key(camera: str, now: datetime) -> str:
    """R2 object key: camera1/2026-05-29/1430Z.png"""
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H%MZ")
    return f"{camera}/{day}/{stamp}.png"


def _newest_etag(r2_client, bucket: str, camera: str) -> str | None:
    """ETag (md5 hex, unquoted) of the most recently stored frame for ``camera``,
    or ``None`` if there are none / the listing fails. Used to skip re-storing a
    byte-identical frame (e.g. a seasonally-off cam whose source never changes)."""
    try:
        newest_key, newest_etag = None, None
        paginator = r2_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=f"{camera}/"):
            for obj in page.get("Contents", []):
                # Keys sort chronologically (zero-padded date + HHMM).
                if newest_key is None or obj["Key"] > newest_key:
                    newest_key, newest_etag = obj["Key"], obj.get("ETag")
        return newest_etag.strip('"') if newest_etag else None
    except Exception:  # noqa: BLE001 - dedup is best-effort; fall back to uploading
        return None


def grab_and_upload(*, r2_client, bucket: str, now: datetime | None = None) -> list[dict]:
    """Fetch each camera and upload it. Returns a per-camera result list."""
    now = now or datetime.now(timezone.utc)
    from .storage import upload_bytes

    results: list[dict] = []
    for camera, url in CAMERAS.items():
        result = {"camera": camera, "uploaded": False, "key": None, "reason": None}
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            result["reason"] = f"request failed: {exc}"
            results.append(result)
            continue

        content_type = resp.headers.get("Content-Type", "")
        if resp.status_code != 200 or not content_type.startswith("image/"):
            # Camera off / out of season / transient error — skip gracefully.
            result["reason"] = f"skip status={resp.status_code} type={content_type or 'none'}"
            results.append(result)
            continue

        # Skip storing a frame identical to the last one we kept. A seasonally-off
        # cam serves the same stale image for weeks; without this we'd archive
        # hundreds of duplicates and clutter the viewer's history slider.
        new_md5 = hashlib.md5(resp.content).hexdigest()
        if new_md5 == _newest_etag(r2_client, bucket, camera):
            result["reason"] = "skip unchanged (identical to latest stored frame)"
            results.append(result)
            continue

        key = frame_key(camera, now)
        upload_bytes(
            r2_client, bucket=bucket, key=key, data=resp.content, content_type="image/png"
        )
        result.update(uploaded=True, key=key)
        results.append(result)
    return results
