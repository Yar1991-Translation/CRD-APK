#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
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
DEFAULT_BUILD_TOOLS_VERSION = "36.1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install apktool and Android build-tools.")
    parser.add_argument("--tools-dir", type=Path, required=True, help="Directory for downloaded tools.")
    parser.add_argument(
        "--build-tools-version",
        default=DEFAULT_BUILD_TOOLS_VERSION,
        help="Preferred Android build-tools version to install.",
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


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(element: ET.Element, name: str) -> str:
    for child in element.iter():
        if local_name(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def detect_host_os() -> str:
    system = platform.system().lower()
    if system == "linux":
        return "linux"
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macosx"
    raise SystemExit(f"Unsupported host OS for Android build-tools: {platform.system()}")


def find_build_tools_archives(host_os: str) -> dict[str, str]:
    root = ET.fromstring(request_bytes(ANDROID_REPO_XML))
    archives: dict[str, str] = {}

    for package in root.iter():
        if local_name(package.tag) != "remotePackage":
            continue
        package_path = package.attrib.get("path", "")
        if not package_path.startswith("build-tools;"):
            continue

        version = package_path.split(";", 1)[1]
        for archive in package.iter():
            if local_name(archive.tag) != "archive":
                continue
            archive_host_os = child_text(archive, "host-os")
            if archive_host_os and archive_host_os != host_os:
                continue
            relative_url = child_text(archive, "url")
            if relative_url:
                archives[version] = f"https://dl.google.com/android/repository/{relative_url}"
                break
    return archives


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def resolve_build_tools_download(preferred_version: str) -> tuple[str, str]:
    host_os = detect_host_os()
    archives = find_build_tools_archives(host_os)
    if not archives:
        raise SystemExit(f"Could not find any {host_os} Android build-tools archives.")

    if preferred_version in archives:
        return preferred_version, archives[preferred_version]

    resolved_version = max(archives, key=version_key)
    print(
        f"Preferred build-tools {preferred_version} is unavailable; "
        f"falling back to {resolved_version}.",
    )
    return resolved_version, archives[resolved_version]


def create_exec_wrapper(wrapper_path: Path, target_path: Path) -> None:
    if platform.system().lower() == "windows":
        wrapper_path = wrapper_path.with_suffix(".cmd")
        wrapper_path.write_text(
            "@echo off\r\n"
            f'"{target_path}" %*\r\n',
            encoding="utf-8",
        )
        return

    wrapper_path.write_text(
        "#!/usr/bin/env bash\n"
        'set -euo pipefail\n'
        f'exec "{target_path}" "$@"\n',
        encoding="utf-8",
    )
    wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def create_apktool_wrapper(wrapper_path: Path, apktool_jar: Path) -> None:
    if platform.system().lower() == "windows":
        wrapper_path = wrapper_path.with_suffix(".cmd")
        wrapper_path.write_text(
            "@echo off\r\n"
            'set "SCRIPT_DIR=%~dp0"\r\n'
            'java -jar "%SCRIPT_DIR%..\\apktool\\apktool.jar" %*\r\n',
            encoding="utf-8",
        )
        return

    wrapper_path.write_text(
        "#!/usr/bin/env bash\n"
        'set -euo pipefail\n'
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'exec java -jar "$SCRIPT_DIR/../apktool/apktool.jar" "$@"\n',
        encoding="utf-8",
    )
    wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def locate_required_tool(root_dir: Path, tool_name: str) -> Path:
    candidate_names = [tool_name]
    if platform.system().lower() == "windows":
        candidate_names.extend([f"{tool_name}.exe", f"{tool_name}.bat", f"{tool_name}.cmd"])

    for candidate_name in candidate_names:
        for candidate in root_dir.rglob(candidate_name):
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

    create_apktool_wrapper(bin_dir / "apktool", apktool_jar)

    resolved_version, build_tools_url = resolve_build_tools_download(args.build_tools_version)
    build_tools_zip = download_dir / f"build-tools-{resolved_version}.zip"
    download(build_tools_url, build_tools_zip)
    with zipfile.ZipFile(build_tools_zip) as archive:
        archive.extractall(build_tools_dir)

    zipalign_path = locate_required_tool(build_tools_dir, "zipalign")
    apksigner_path = locate_required_tool(build_tools_dir, "apksigner")
    create_exec_wrapper(bin_dir / "zipalign", zipalign_path)
    create_exec_wrapper(bin_dir / "apksigner", apksigner_path)

    print(bin_dir)
    print(build_tools_dir)
    print(resolved_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
