"""Build a manifest of captured webcam frames stored in Cloudflare R2.

The browser can't list a private bucket, so the viewer build lists frames here
(with R2 credentials) and emits an ordered index. The frontend then shows the
newest frame and steps backwards through history, building image URLs as
``{public_base_url}/{key}``.

Frame keys follow ``camera{n}/YYYY-MM-DD/HHMMZ.jpg`` (``.png`` before 2026-10-17;
see :mod:`powwx.webcam`).

To keep ``webcams.json`` from growing without bound (review F16), the index lists
every frame from the last ``RECENT_DAYS`` and one frame per day before that (the
one nearest local midday). Older frames stay in R2; only the index is thinned.
"""

from __future__ import annotations

import os

from .webcam import CAMERAS

CAMERA_LABELS = {"camera1": "Chairlift", "camera2": "Parking lot"}
RECENT_DAYS = 30
KEYFRAME_UTC_MINUTES = 20 * 60  # 20:00 UTC = midday in BC (12:00 PST / 13:00 PDT)


def _key_to_time(key: str) -> str | None:
    """camera1/2026-05-29/1932Z.jpg -> 2026-05-29T19:32Z (None if unparseable)."""
    parts = key.split("/")
    if len(parts) != 3:
        return None
    _, day, fname = parts
    hhmm = fname.removesuffix(".jpg").removesuffix(".png").removesuffix("Z")
    if len(hhmm) != 4 or not hhmm.isdigit():
        return None
    return f"{day}T{hhmm[:2]}:{hhmm[2:]}Z"


def _list_camera_frames(client, bucket: str, camera: str, max_frames: int | None) -> list[dict]:
    """Return frames for one camera, newest first."""
    frames: list[dict] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{camera}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            ts = _key_to_time(key)
            if ts is None:
                continue
            frames.append({"key": key, "time": ts})
    # Keys sort chronologically (zero-padded date + HHMM), so reverse = newest first.
    # Sort on the time, not the key, so .png and .jpg frames interleave correctly.
    frames.sort(key=lambda f: f["time"], reverse=True)
    frames = thin_frames(frames)
    if max_frames is not None:
        frames = frames[:max_frames]
    return frames


def thin_frames(frames: list[dict], *, recent_days: int = RECENT_DAYS) -> list[dict]:
    """Keep every frame from the ``recent_days`` before the newest one, and one
    frame per day before that (nearest KEYFRAME_UTC_MINUTES). Input and output
    are newest first."""
    if not frames:
        return frames
    from datetime import date, timedelta

    newest_day = date.fromisoformat(frames[0]["time"][:10])
    cutoff = (newest_day - timedelta(days=recent_days)).isoformat()
    recent = [f for f in frames if f["time"][:10] > cutoff]
    keyframes: dict[str, dict] = {}
    for f in frames:
        day = f["time"][:10]
        if day > cutoff:
            continue
        minutes = int(f["time"][11:13]) * 60 + int(f["time"][14:16])
        best = keyframes.get(day)
        if best is None or abs(minutes - KEYFRAME_UTC_MINUTES) < best["_d"]:
            keyframes[day] = {**f, "_d": abs(minutes - KEYFRAME_UTC_MINUTES)}
    older = [{k: v for k, v in f.items() if k != "_d"}
             for _, f in sorted(keyframes.items(), reverse=True)]
    return recent + older


def base_cameras() -> dict:
    """Camera entries with label + live (GCS) URL and no history yet. Used as the
    fallback when R2 isn't configured, and as the base that R2 frames merge into."""
    return {
        camera: {
            "label": CAMERA_LABELS.get(camera, camera),
            "live_url": CAMERAS[camera],
            "count": 0,
            "frames": [],
        }
        for camera in CAMERAS
    }


def build_webcam_index(*, client, bucket: str, public_base_url: str,
                       max_frames: int | None = None) -> dict:
    """Return the webcams manifest dict for ``data/webcams.json``."""
    cameras = base_cameras()
    for camera in CAMERAS:
        frames = _list_camera_frames(client, bucket, camera, max_frames)
        cameras[camera]["frames"] = frames
        cameras[camera]["count"] = len(frames)
    return {
        "public_base_url": public_base_url.rstrip("/"),
        "cameras": cameras,
    }


def public_base_url_from_env() -> str:
    """R2_PUBLIC_BASE_URL env var (the bucket's r2.dev or custom-domain URL)."""
    url = os.environ.get("R2_PUBLIC_BASE_URL", "")
    return url.strip()
