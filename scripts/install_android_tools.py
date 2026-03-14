#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import stat
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


APKTOOL_RELEASE_API = "https://api.github.com/repos/iBotPeaches/Apktool/releases/latest"
ANDROID_REPO_XML = "https://dl.google.com/android/repository/repository2-1.xml"
RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install apktool and Android build-tools.")
    parser.add_argument("--tools-dir", type=Path, required=True, help="Directory for downloaded tools.")
    parser.add_argument(
        "--build-tools-version",
        default="36.1.0",
        help="Android build-tools version to install.",
    )
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


def download(url: str, destination: Path) -> None:
    destination.write_bytes(request_bytes(url))


def fetch_json(url: str) -> dict:
    return json.loads(request_bytes(url).decode("utf-8"))


def fetch_build_tools_url(version: str) -> str:
    root = ET.fromstring(request_bytes(ANDROID_REPO_XML))
    namespace = {
        "sdk": "http://schemas.android.com/sdk/android/repo/repository2/01",
        "common": "http://schemas.android.com/repository/android/common/01",
    }
    package_path = f"build-tools;{version}"
    for package in root.findall("sdk:remotePackage", namespace):
        if package.attrib.get("path") != package_path:
            continue
        for archive in package.findall("sdk:archives/sdk:archive", namespace):
            host_os = archive.findtext("common:host-os", default="", namespaces=namespace)
            if host_os != "linux":
                continue
            relative_url = archive.findtext("sdk:complete/common:url", default="", namespaces=namespace)
            if relative_url:
                return f"https://dl.google.com/android/repository/{relative_url}"
    raise SystemExit(f"Could not find Linux build-tools archive for version {version}")


def create_exec_wrapper(wrapper_path: Path, target_path: Path) -> None:
    wrapper_path.write_text(
        "#!/usr/bin/env bash\n"
        'set -euo pipefail\n'
        f'exec "{target_path}" "$@"\n',
        encoding="utf-8",
    )
    wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def locate_required_tool(root_dir: Path, tool_name: str) -> Path:
    for candidate in root_dir.rglob(tool_name):
        if candidate.is_file():
            candidate.chmod(
                candidate.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
            return candidate.resolve()
    raise SystemExit(f"Could not find {tool_name} in extracted Android build-tools")


def main() -> int:
    args = parse_args()
    tools_dir = args.tools_dir.resolve()
    download_dir = tools_dir / "downloads"
    bin_dir = tools_dir / "bin"
    apktool_dir = tools_dir / "apktool"
    build_tools_dir = tools_dir / "build-tools"

    if tools_dir.exists():
        shutil.rmtree(tools_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    apktool_dir.mkdir(parents=True, exist_ok=True)
    build_tools_dir.mkdir(parents=True, exist_ok=True)

    release = fetch_json(APKTOOL_RELEASE_API)
    apktool_asset = next((item for item in release["assets"] if item["name"].endswith(".jar")), None)
    if apktool_asset is None:
        raise SystemExit("Could not find apktool jar in the latest release")

    apktool_jar = apktool_dir / "apktool.jar"
    download(apktool_asset["browser_download_url"], apktool_jar)

    apktool_wrapper = bin_dir / "apktool"
    apktool_wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'set -euo pipefail\n'
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'exec java -jar "$SCRIPT_DIR/../apktool/apktool.jar" "$@"\n',
        encoding="utf-8",
    )
    apktool_wrapper.chmod(apktool_wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    build_tools_zip = download_dir / f"build-tools-{args.build_tools_version}.zip"
    download(fetch_build_tools_url(args.build_tools_version), build_tools_zip)
    with zipfile.ZipFile(build_tools_zip) as archive:
        archive.extractall(build_tools_dir)

    zipalign_path = locate_required_tool(build_tools_dir, "zipalign")
    apksigner_path = locate_required_tool(build_tools_dir, "apksigner")
    create_exec_wrapper(bin_dir / "zipalign", zipalign_path)
    create_exec_wrapper(bin_dir / "apksigner", apksigner_path)

    print(bin_dir)
    print(build_tools_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
