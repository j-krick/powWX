"""Webcam frames are stored as JPEG (much smaller than the source PNG), unchanged
frames are still skipped, and the index thins old history (review F16)."""

import hashlib
import io
from datetime import datetime, timezone
from unittest import mock

from PIL import Image

from powwx import storage
from powwx import webcam as wc
from powwx import webcam_index as wi


def _png(width=1280, height=720):
    im = Image.new("RGB", (width, height))
    for x in range(0, width, 8):               # some detail so PNG isn't trivially small
        for y in range(0, height, 8):
            im.putpixel((x, y), ((x * 7) % 256, (y * 5) % 256, (x + y) % 256))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def test_to_jpeg_caps_width_and_is_deterministic():
    jpg = wc.to_jpeg(_png())
    im = Image.open(io.BytesIO(jpg))
    assert im.format == "JPEG" and im.size == (960, 540)
    assert wc.to_jpeg(_png()) == jpg           # same input -> same bytes (dedup relies on it)


def _grab(newest_etag):
    resp = mock.Mock(status_code=200, headers={"Content-Type": "image/png"}, content=_png())
    with mock.patch.object(wc.requests, "get", return_value=resp), \
         mock.patch.object(wc, "_newest_etag", return_value=newest_etag), \
         mock.patch.object(storage, "upload_bytes") as up:
        res = wc.grab_and_upload(r2_client=None, bucket="b",
                                 now=datetime(2026, 10, 17, 14, 30, tzinfo=timezone.utc))
    return res, up


def test_frames_upload_as_jpeg_and_unchanged_frames_skip():
    res, up = _grab(newest_etag=None)
    assert all(r["uploaded"] and r["key"].endswith("/2026-10-17/1430Z.jpg") for r in res)
    assert up.call_args.kwargs["content_type"] == "image/jpeg"

    same = hashlib.md5(wc.to_jpeg(_png())).hexdigest()
    res, up = _grab(newest_etag=same)
    assert not any(r["uploaded"] for r in res) and not up.called


def test_index_reads_png_and_jpg_keys():
    assert wi._key_to_time("camera1/2026-05-29/1932Z.png") == "2026-05-29T19:32Z"
    assert wi._key_to_time("camera1/2026-10-17/0930Z.jpg") == "2026-10-17T09:30Z"


def test_thin_frames_keeps_recent_and_one_midday_frame_per_older_day():
    frames = []
    for day in ("2026-10-17", "2026-10-10", "2026-08-01"):
        for hhmm in ("1500", "2030", "2300"):
            frames.append({"key": f"camera2/{day}/{hhmm}Z.jpg", "time": f"{day}T{hhmm[:2]}:{hhmm[2:]}Z"})
    frames.sort(key=lambda f: f["time"], reverse=True)
    out = wi.thin_frames(frames, recent_days=30)
    days = [f["time"][:10] for f in out]
    assert days.count("2026-10-17") == 3 and days.count("2026-10-10") == 3   # recent: all
    assert [f["time"] for f in out if f["time"].startswith("2026-08-01")] == ["2026-08-01T20:30Z"]
    assert [f["time"] for f in out] == sorted((f["time"] for f in out), reverse=True)
