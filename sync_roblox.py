#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


APP_NAME = "Roblox"
PACKAGE_NAME = "com.roblox.client"
SOURCE_NAME = "APKPure"
APKPURE_VERSION_URL = (
    "https://api.pureapk.com/m/v3/cms/app_version?hl=en-US&package_name="
    f"{PACKAGE_NAME}"
)
APKPURE_HEADERS = {
    "x-cv": "3172501",
    "x-sv": "29",
    "x-abis": "arm64-v8a,armeabi-v7a,armeabi,x86,x86_64",
    "x-gp": "1",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}
VERSION_PATTERN = re.compile(
    r"com\.roblox\.client\*[^0-9]{0,4}([0-9]+)\t([0-9][0-9A-Za-z._-]+):.{0,8000}?"
    r"(XAPKJ|APKJ).{0,8}(https://download\.pureapk\.com/b/(?:XAPK|APK)/"
    r"[A-Za-z0-9?&=._%/\-]+)",
    re.S,
)

ROOT = Path(__file__).resolve().parent
LATEST_VERSION_FILE = ROOT / "latest_version.txt"
DIST_DIR = ROOT / "dist"
WORK_DIR = ROOT / ".work"
RELEASE_NOTES_DIR = ROOT / "release-notes"
CONVERTED_DIR = ROOT / "converted-apks"
MERGED_DIR = ROOT / "merged-apks"
MERGEAPKS_SCRIPT = ROOT / "mergeapks.py"
MERGEAPKS_SIGN_PROPERTIES_ENV = "MERGEAPKS_SIGN_PROPERTIES"
SUPPORTED_SPLIT_ARCHIVE_SUFFIXES = {".xapk", ".apks", ".zapk"}


class SyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version_name: str
    version_code: str
    download_url: str
    file_ext: str
    source: str = SOURCE_NAME

    @property
    def version_id(self) -> str:
        return f"{self.version_name}+{self.version_code}"

    @property
    def tag_name(self) -> str:
        return self.version_name

    @property
    def artifact_name(self) -> str:
        return f"roblox-android-v{self.version_name}{self.file_ext}"


def build_session() -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update(APKPURE_HEADERS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def write_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def read_latest_version_file(path: Path) -> dict[str, str]:
    result = {"version_name": "", "version_code": ""}
    if not path.exists():
        return result

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in result:
            result[key] = value.strip()
    return result


def write_latest_version_file(path: Path, info: ReleaseInfo) -> None:
    path.write_text(
        f"version_name={info.version_name}\nversion_code={info.version_code}\n",
        encoding="utf-8",
    )


def fetch_latest_release_info(session: requests.Session) -> ReleaseInfo:
    response = session.get(APKPURE_VERSION_URL, timeout=30)
    response.raise_for_status()
    text = response.content.decode("latin-1", errors="ignore")

    matches: list[ReleaseInfo] = []
    for match in VERSION_PATTERN.finditer(text):
        version_code, version_name, marker, download_url = match.groups()
        file_ext = ".xapk" if marker == "XAPKJ" else ".apk"
        matches.append(
            ReleaseInfo(
                version_name=version_name,
                version_code=version_code,
                download_url=download_url,
                file_ext=file_ext,
            )
        )

    if not matches:
        raise SyncError("Could not parse the latest Roblox version from APKPure.")

    latest = max(matches, key=lambda item: int(item.version_code))
    return latest


def ensure_clean_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_member_output_path(member_name: str) -> Path:
    member_path = PurePosixPath(member_name)
    safe_parts = [part for part in member_path.parts if part not in {"", ".", ".."}]
    if not safe_parts:
        raise SyncError(f"Invalid archive member path: {member_name}")
    return Path(*safe_parts)


def copy_stream_to_path(source, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        shutil.copyfileobj(source, target)


def read_archive_manifest(archive: zipfile.ZipFile) -> dict | None:
    for name in archive.namelist():
        if PurePosixPath(name).name == "manifest.json":
            try:
                return json.loads(archive.read(name))
            except json.JSONDecodeError:
                return None
    return None


def resolve_manifest_base_entry(
    apk_entries: list[str],
    manifest: dict | None,
) -> str | None:
    if not manifest:
        return None

    split_apks = manifest.get("split_apks")
    if not isinstance(split_apks, list):
        return None

    manifest_base_files = [
        item.get("file")
        for item in split_apks
        if isinstance(item, dict) and item.get("id") == "base" and item.get("file")
    ]
    if not manifest_base_files:
        return None

    target_name = PurePosixPath(manifest_base_files[0]).name.lower()
    for entry in apk_entries:
        if PurePosixPath(entry).name.lower() == target_name:
            return entry
    return None


def choose_primary_apk_entry(
    apk_entries: list[str],
    manifest: dict | None,
) -> tuple[str, str]:
    if len(apk_entries) == 1:
        return apk_entries[0], "single_apk"

    universal_candidates = [
        entry
        for entry in apk_entries
        if "universal" in PurePosixPath(entry).name.lower()
    ]
    if len(universal_candidates) == 1:
        return universal_candidates[0], "universal_apk"

    manifest_entry = resolve_manifest_base_entry(apk_entries, manifest)
    if manifest_entry is not None:
        return manifest_entry, "split_package"

    preferred_names = {
        "base.apk",
        "base-master.apk",
        f"{PACKAGE_NAME}.apk",
        f"{PACKAGE_NAME}-master.apk",
    }
    for entry in apk_entries:
        if PurePosixPath(entry).name.lower() in preferred_names:
            return entry, "split_package"

    for entry in apk_entries:
        basename = PurePosixPath(entry).name.lower()
        if basename.startswith("base-") and basename.endswith(".apk"):
            return entry, "split_package"

    return sorted(apk_entries)[0], "split_package"


def convert_archive_to_apk(
    archive_path: Path,
    output_dir: Path,
) -> dict[str, Path | str | int]:
    if not archive_path.exists():
        raise SyncError(f"Archive does not exist: {archive_path}")

    suffix = archive_path.suffix.lower()
    output_dir.mkdir(parents=True, exist_ok=True)

    if suffix == ".apk":
        destination = output_dir / archive_path.name
        shutil.copy2(archive_path, destination)
        return {
            "mode": "apk_copy",
            "primary_apk": destination,
            "metadata_path": Path(),
            "split_count": 1,
        }

    if suffix not in SUPPORTED_SPLIT_ARCHIVE_SUFFIXES:
        raise SyncError("Only .apk, .xapk, .apks, and .zapk files are supported for conversion.")

    extract_dir = output_dir / archive_path.stem
    ensure_clean_dir(extract_dir)

    with zipfile.ZipFile(archive_path) as archive:
        apk_entries = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and PurePosixPath(name).name.lower().endswith(".apk")
        ]
        if not apk_entries:
            raise SyncError(f"No APK files were found in archive: {archive_path.name}")

        manifest = read_archive_manifest(archive)
        primary_entry, mode = choose_primary_apk_entry(apk_entries, manifest)

        extracted_apks: list[Path] = []
        for entry in apk_entries:
            relative_path = archive_member_output_path(entry)
            destination = extract_dir / relative_path
            with archive.open(entry) as source:
                copy_stream_to_path(source, destination)
            extracted_apks.append(destination)

        primary_name = PurePosixPath(primary_entry).name
        primary_apk = output_dir / f"{archive_path.stem}.apk"
        source_primary = extract_dir / archive_member_output_path(primary_entry)
        shutil.copy2(source_primary, primary_apk)

        metadata_path = extract_dir / "conversion-result.json"
        metadata = {
            "input_archive": str(archive_path),
            "mode": mode,
            "primary_apk": str(primary_apk),
            "extracted_directory": str(extract_dir),
            "selected_entry": primary_entry,
            "selected_filename": primary_name,
            "split_count": len(apk_entries),
            "all_apks": [str(path) for path in extracted_apks],
        }
        if mode == "split_package":
            metadata["warning"] = (
                "This archive contains split APKs. A single universal APK cannot be "
                "reliably reconstructed from split packages alone. The exported APK is "
                "the base APK, and it usually must be installed together with the other "
                "split APK files in the extracted directory."
            )

        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    return {
        "mode": mode,
        "primary_apk": primary_apk,
        "metadata_path": metadata_path,
        "split_count": len(apk_entries),
    }


def extract_archive_apks(
    archive_path: Path,
    output_dir: Path,
) -> tuple[Path, list[Path], str]:
    if not archive_path.exists():
        raise SyncError(f"Archive does not exist: {archive_path}")

    suffix = archive_path.suffix.lower()
    ensure_clean_dir(output_dir)

    if suffix == ".apk":
        destination = output_dir / archive_path.name
        shutil.copy2(archive_path, destination)
        return destination, [destination], "single_apk"

    if suffix not in SUPPORTED_SPLIT_ARCHIVE_SUFFIXES:
        raise SyncError("Only .apk, .xapk, .apks, and .zapk files are supported for conversion.")

    with zipfile.ZipFile(archive_path) as archive:
        apk_entries = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and PurePosixPath(name).name.lower().endswith(".apk")
        ]
        if not apk_entries:
            raise SyncError(f"No APK files were found in archive: {archive_path.name}")

        manifest = read_archive_manifest(archive)
        primary_entry, mode = choose_primary_apk_entry(apk_entries, manifest)
        extracted: dict[str, Path] = {}
        for entry in apk_entries:
            relative_path = archive_member_output_path(entry)
            destination = output_dir / relative_path
            with archive.open(entry) as source:
                copy_stream_to_path(source, destination)
            extracted[entry] = destination

    ordered_apks = [extracted[primary_entry]] + [
        extracted[entry] for entry in apk_entries if entry != primary_entry
    ]
    return extracted[primary_entry], ordered_apks, mode


def merge_archive_with_apktool(archive_path: Path, output_dir: Path) -> dict[str, Path | str | int]:
    if not MERGEAPKS_SCRIPT.exists():
        raise SyncError(f"mergeapks.py was not found: {MERGEAPKS_SCRIPT}")

    extract_dir = WORK_DIR / "mergeapks-input"
    primary_apk, extracted_apks, mode = extract_archive_apks(archive_path, extract_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{archive_path.stem}-merged.apk"
    if destination.exists():
        destination.unlink()

    if len(extracted_apks) < 2:
        shutil.copy2(primary_apk, destination)
        return {
            "mode": "single_apk",
            "primary_apk": destination,
            "metadata_path": Path(),
            "split_count": 1,
        }

    sign_properties_env = os.getenv(MERGEAPKS_SIGN_PROPERTIES_ENV, "").strip()
    sign_properties = (
        Path(sign_properties_env)
        if sign_properties_env
        else ROOT / "mergeapks.sign.properties"
    )
    if sign_properties.exists():
        shutil.copy2(sign_properties, extract_dir / sign_properties.name)

    result = subprocess.run(
        [sys.executable, str(MERGEAPKS_SCRIPT), *[path.name for path in extracted_apks]],
        cwd=extract_dir,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise SyncError(
            "mergeapks.py failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    merged_apk = extract_dir / "result.apk"
    if not merged_apk.exists():
        raise SyncError("mergeapks.py completed without producing result.apk")

    shutil.move(str(merged_apk), destination)
    metadata_path = output_dir / f"{archive_path.stem}-mergeapks.json"
    metadata_path.write_text(
        json.dumps(
            {
                "input_archive": str(archive_path),
                "mode": mode,
                "merge_method": "apktool",
                "merged_apk": str(destination),
                "split_count": len(extracted_apks),
                "input_apks": [str(path) for path in extracted_apks],
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "mode": mode,
        "primary_apk": destination,
        "metadata_path": metadata_path,
        "split_count": len(extracted_apks),
    }


def download_with_apkeep(info: ReleaseInfo, out_dir: Path) -> list[Path]:
    executable = shutil.which("apkeep")
    if not executable:
        raise SyncError("apkeep is not installed or not available in PATH.")

    command = [
        executable,
        "-a",
        f"{PACKAGE_NAME}@{info.version_name}",
        "-d",
        "apk-pure",
        str(out_dir),
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise SyncError(
            "apkeep download failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    artifacts = sorted(
        file_path
        for file_path in out_dir.rglob("*")
        if file_path.is_file()
        and file_path.suffix.lower() in {".apk", *SUPPORTED_SPLIT_ARCHIVE_SUFFIXES}
    )
    if not artifacts:
        raise SyncError("apkeep finished without producing any APK or XAPK files.")
    return artifacts


def direct_download(
    session: requests.Session,
    info: ReleaseInfo,
    out_dir: Path,
) -> list[Path]:
    target = out_dir / f"{PACKAGE_NAME}-{info.version_name}{info.file_ext}"
    attempts = 3
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        partial_target = target.with_suffix(target.suffix + ".part")
        if partial_target.exists():
            partial_target.unlink()
        try:
            with session.get(info.download_url, stream=True, timeout=120) as response:
                response.raise_for_status()
                with partial_target.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            if target.exists():
                target.unlink()
            partial_target.rename(target)
            return [target]
        except Exception as exc:
            last_error = exc
            if partial_target.exists():
                partial_target.unlink()
            if attempt == attempts:
                break
            print(f"Direct download attempt {attempt} failed, retrying...")
    raise SyncError(f"Direct download failed after {attempts} attempts: {last_error}")


def infer_split_id(path: Path) -> str:
    stem = path.stem
    if stem == "base":
        return "base"
    return stem.replace(".", "_")


def package_split_apks(
    apk_files: Iterable[Path],
    extra_files: Iterable[Path],
    destination: Path,
    info: ReleaseInfo,
) -> Path:
    apk_files = sorted(apk_files)
    extra_files = sorted(extra_files)
    if not apk_files:
        raise SyncError("No APK files were provided for XAPK packaging.")

    total_size = sum(path.stat().st_size for path in apk_files) + sum(
        path.stat().st_size for path in extra_files
    )
    manifest = {
        "xapk_version": 2,
        "package_name": PACKAGE_NAME,
        "name": APP_NAME,
        "version_name": info.version_name,
        "version_code": info.version_code,
        "split_apks": [
            {
                "file": path.name,
                "id": infer_split_id(path),
                "size": path.stat().st_size,
            }
            for path in apk_files
        ],
        "total_size": total_size,
    }

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        )
        for path in apk_files:
            archive.write(path, arcname=path.name)
        for path in extra_files:
            archive.write(path, arcname=path.name)
    return destination


def normalize_artifacts(artifacts: list[Path], info: ReleaseInfo, final_dir: Path) -> Path:
    ensure_clean_dir(final_dir)
    apk_files = [path for path in artifacts if path.suffix.lower() == ".apk"]
    archive_files = [
        path for path in artifacts if path.suffix.lower() in SUPPORTED_SPLIT_ARCHIVE_SUFFIXES
    ]

    if len(apk_files) == 1 and not archive_files:
        destination = final_dir / f"roblox-android-v{info.version_name}.apk"
        shutil.copy2(apk_files[0], destination)
        return destination

    if len(archive_files) == 1 and not apk_files:
        source_archive = archive_files[0]
        destination = final_dir / f"roblox-android-v{info.version_name}{source_archive.suffix.lower()}"
        shutil.copy2(source_archive, destination)
        return destination

    if apk_files:
        destination = final_dir / f"roblox-android-v{info.version_name}.xapk"
        extra_files = [
            path
            for path in artifacts
            if path.suffix.lower() not in {".apk", *SUPPORTED_SPLIT_ARCHIVE_SUFFIXES}
        ]
        return package_split_apks(apk_files, extra_files, destination, info)

    raise SyncError("Could not normalize downloaded artifacts into an APK or XAPK.")


def write_release_notes(path: Path, info: ReleaseInfo, artifact: Path, sha256: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {APP_NAME} Android {info.version_name}",
                "",
                f"- Package: `{PACKAGE_NAME}`",
                f"- Source: {info.source}",
                f"- Version Name: `{info.version_name}`",
                f"- Version Code: `{info.version_code}`",
                f"- Artifact: `{artifact.name}`",
                f"- SHA256: `{sha256}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def export_outputs(
    has_update: bool,
    info: ReleaseInfo | None = None,
    artifact: Path | None = None,
    sha256: str = "",
    release_notes_path: Path | None = None,
) -> None:
    write_output("has_update", "true" if has_update else "false")
    if info is None:
        return
    write_output("version_name", info.version_name)
    write_output("version_code", info.version_code)
    write_output("tag_name", info.tag_name)
    write_output("source_name", info.source)
    if artifact is not None:
        write_output("artifact_path", str(artifact))
        write_output("artifact_name", artifact.name)
    if sha256:
        write_output("sha256", sha256)
    if release_notes_path is not None:
        write_output("release_notes_path", str(release_notes_path))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor Roblox Android updates and prepare release artifacts."
    )
    parser.add_argument(
        "--convert-archive",
        type=Path,
        help="Convert an .apk/.xapk/.apks file into an extracted APK output.",
    )
    parser.add_argument(
        "--convert-output-dir",
        type=Path,
        default=None,
        help="Directory used by --convert-archive. Defaults depend on convert method.",
    )
    parser.add_argument(
        "--convert-method",
        choices=("extract", "apktool"),
        default="extract",
        help="Conversion strategy used by --convert-archive. Defaults to extract.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Download the latest archive even when latest_version.txt already matches.",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download the latest artifact and stop before generating release notes.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Fetch and compare the latest version without downloading the artifact.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.convert_archive:
        if args.check_only:
            raise SyncError("--check-only cannot be used together with --convert-archive.")

        convert_output_dir = args.convert_output_dir
        if convert_output_dir is None:
            convert_output_dir = MERGED_DIR if args.convert_method == "apktool" else CONVERTED_DIR

        if args.convert_method == "apktool":
            result = merge_archive_with_apktool(args.convert_archive, convert_output_dir)
        else:
            result = convert_archive_to_apk(args.convert_archive, convert_output_dir)
        primary_apk = result["primary_apk"]
        metadata_path = result["metadata_path"]
        mode = result["mode"]
        split_count = result["split_count"]

        write_output("conversion_mode", str(mode))
        write_output("converted_apk_path", str(primary_apk))
        write_output("converted_split_count", str(split_count))
        if isinstance(metadata_path, Path) and metadata_path != Path():
            write_output("conversion_metadata_path", str(metadata_path))

        print(f"Converted archive mode: {mode}")
        print(f"Primary APK: {primary_apk}")
        if isinstance(metadata_path, Path) and metadata_path != Path():
            print(f"Metadata: {metadata_path}")
        return 0

    session = build_session()
    current = read_latest_version_file(LATEST_VERSION_FILE)

    info = fetch_latest_release_info(session)
    is_same_version = (
        current["version_name"] == info.version_name
        and current["version_code"] == info.version_code
    )

    if is_same_version and not args.force_download:
        print(f"No update detected. Latest version is still {info.version_name}.")
        export_outputs(has_update=False, info=info)
        return 0

    if args.check_only:
        print(
            "Update available: "
            f"{current['version_name'] or 'none'} -> {info.version_name}"
        )
        export_outputs(has_update=True, info=info)
        return 0

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    download_dir = WORK_DIR / "downloads"
    ensure_clean_dir(download_dir)

    artifacts: list[Path]
    try:
        artifacts = download_with_apkeep(info, download_dir)
        print(f"Downloaded artifact with apkeep for version {info.version_name}.")
    except Exception as exc:
        print(f"apkeep download failed, falling back to direct download: {exc}")
        ensure_clean_dir(download_dir)
        artifacts = direct_download(session, info, download_dir)
        print(f"Downloaded artifact directly from APKPure for version {info.version_name}.")

    artifact = normalize_artifacts(artifacts, info, DIST_DIR)
    sha256 = file_sha256(artifact)
    write_latest_version_file(LATEST_VERSION_FILE, info)

    if args.download_only:
        export_outputs(
            has_update=True,
            info=info,
            artifact=artifact,
            sha256=sha256,
        )
        print(f"Prepared artifact: {artifact}")
        print(f"SHA256: {sha256}")
        return 0

    release_notes_path = RELEASE_NOTES_DIR / f"{info.version_name}.md"
    write_release_notes(release_notes_path, info, artifact, sha256)

    export_outputs(
        has_update=True,
        info=info,
        artifact=artifact,
        sha256=sha256,
        release_notes_path=release_notes_path,
    )

    print(f"Prepared artifact: {artifact}")
    print(f"SHA256: {sha256}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.RequestException as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except SyncError as exc:
        print(f"Sync error: {exc}", file=sys.stderr)
        raise SystemExit(1)
