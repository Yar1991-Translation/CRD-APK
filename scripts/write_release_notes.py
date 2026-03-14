#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write GitHub release notes for Roblox Android.")
    parser.add_argument("--output", type=Path, required=True, help="Release notes file path.")
    parser.add_argument("--package-name", required=True, help="Android package name.")
    parser.add_argument("--version-name", required=True, help="Version name.")
    parser.add_argument("--version-code", required=True, help="Version code.")
    parser.add_argument("--source", required=True, help="Download source name.")
    parser.add_argument("--archive-name", default="", help="Downloaded split archive name.")
    parser.add_argument("--archive-sha256", default="", help="Downloaded split archive SHA256.")
    parser.add_argument("--apk-name", required=True, help="Release APK name.")
    parser.add_argument("--apk-sha256", required=True, help="Release APK SHA256.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Roblox Android {args.version_name}",
        "",
        f"- Package: `{args.package_name}`",
        f"- Source: {args.source}",
        f"- Version Name: `{args.version_name}`",
        f"- Version Code: `{args.version_code}`",
    ]
    if args.archive_name:
        lines.append(f"- Split Archive: `{args.archive_name}`")
        lines.append(f"- Split Archive SHA256: `{args.archive_sha256}`")
    lines.append(f"- APK: `{args.apk_name}`")
    lines.append(f"- APK SHA256: `{args.apk_sha256}`")
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
