#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write mergeapks sign properties.")
    parser.add_argument("--output", type=Path, required=True, help="Output properties file path.")
    parser.add_argument("--keystore", type=Path, required=True, help="Keystore file path.")
    parser.add_argument("--storepass", required=True, help="Keystore password.")
    parser.add_argument("--alias", required=True, help="Key alias.")
    parser.add_argument("--keypass", required=True, help="Key password.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(
            [
                "sign.enabled=true",
                f"sign.keystore.file={args.keystore.resolve()}",
                f"sign.keystore.password={args.storepass}",
                f"sign.key.alias={args.alias}",
                f"sign.key.password={args.keypass}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
