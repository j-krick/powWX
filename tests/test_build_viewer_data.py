"""A webcam/R2 failure must not sink the viewer build (review F11)."""

import importlib.util

from powwx import config as cfg
from powwx import storage

_spec = importlib.util.spec_from_file_location(
    "build_viewer_data", cfg.REPO_ROOT / "scripts" / "build_viewer_data.py")
bvd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bvd)


def test_r2_failure_falls_back_to_live_frames(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "x")
    monkeypatch.setenv("R2_BUCKET", "b")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://example.invalid")

    def expired():
        raise RuntimeError("token expired")

    monkeypatch.setattr(storage, "get_r2_client", expired)
    out = bvd.build_webcams()
    assert out["public_base_url"] == ""
    assert out["cameras"]                          # live cameras still listed
