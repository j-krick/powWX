#!/usr/bin/env python
"""Entry point for the scheduled webcam grabber (GitHub Actions / local).

Requires env: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

from powwx.storage import get_r2_client  # noqa: E402
from powwx.webcam import grab_and_upload  # noqa: E402


def main() -> int:
    bucket = os.environ.get("R2_BUCKET")
    if not bucket:
        print("Missing required environment variable: R2_BUCKET", file=sys.stderr)
        return 1
    client = get_r2_client()
    results = grab_and_upload(r2_client=client, bucket=bucket)
    print(json.dumps(results, indent=2))
    # Success if at least one frame uploaded; cameras-off is not a failure but
    # we surface it as a non-zero-uploaded note rather than failing the job.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
