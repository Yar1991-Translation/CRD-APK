#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import stat
import time
import urllib.request
from pathlib import Path


GITHUB_API = "https://api.github.com/repos/EFForg/apkeep/releases/latest"
RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install apkeep into a target directory.")
    parser.add_argument("--bin-dir", type=Path, required=True, help="Directory for the apkeep binary.")
    return parser.parse_args()


def request_bytes(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "RB-APK"})
            with urllib.request.urlopen(request) as response:
                return response.read()
        except Exception as exc:  # pragma: no cover - exercised in CI/network failures
            last_error = exc
            if attempt == RETRY_COUNT:
                break
            time.sleep(RETRY_DELAY_SECONDS * attempt)
    raise SystemExit(f"Failed to download {url}: {last_error}")


def detect_asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system != "linux":
        raise SystemExit(f"Unsupported platform for this installer: {platform.system()}")
    if machine in {"x86_64", "amd64"}:
        return "apkeep-x86_64-unknown-linux-gnu"
    raise SystemExit(f"Unsupported architecture for this installer: {platform.machine()}")


def fetch_release_json() -> dict:
    return json.loads(request_bytes(GITHUB_API).decode("utf-8"))


def download(url: str, destination: Path) -> None:
    destination.write_bytes(request_bytes(url))


def main() -> int:
    args = parse_args()
    args.bin_dir.mkdir(parents=True, exist_ok=True)

    asset_name = detect_asset_name()
    release = fetch_release_json()
    asset = next((item for item in release["assets"] if item["name"] == asset_name), None)
    if asset is None:
        raise SystemExit(f"Could not find release asset {asset_name}")

    destination = args.bin_dir / "apkeep"
    download(asset["browser_download_url"], destination)
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
