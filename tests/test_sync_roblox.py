import json
import unittest
import zipfile

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import sync_roblox


class SyncRobloxTests(unittest.TestCase):
    @mock.patch("sync_roblox.read_apk_manifest_info")
    def test_package_split_apks_preserves_manifest_split_ids(self, mock_manifest_info):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            base_apk = temp_path / "base.apk"
            config_apk = temp_path / "config.arm64_v8a.apk"
            destination = temp_path / "roblox.xapk"
            base_apk.write_bytes(b"base")
            config_apk.write_bytes(b"config")

            def side_effect(path: Path) -> sync_roblox.ApkManifestInfo:
                if path == base_apk:
                    return sync_roblox.ApkManifestInfo(
                        package_name=sync_roblox.PACKAGE_NAME,
                        version_name="2.711.876",
                        version_code="2036",
                        split_name=None,
                    )
                return sync_roblox.ApkManifestInfo(
                    package_name=sync_roblox.PACKAGE_NAME,
                    version_name="2.711.876",
                    version_code="2036",
                    split_name="config.arm64_v8a",
                )

            mock_manifest_info.side_effect = side_effect
            info = sync_roblox.ReleaseInfo(
                version_name="2.711.876",
                version_code="2036",
                download_url="https://example.invalid/roblox.xapk",
                file_ext=".xapk",
            )

            sync_roblox.package_split_apks([base_apk, config_apk], [], destination, info)

            with zipfile.ZipFile(destination) as archive:
                manifest = json.loads(archive.read("manifest.json"))

            self.assertEqual(
                ["base", "config.arm64_v8a"],
                [entry["id"] for entry in manifest["split_apks"]],
            )

    def test_discover_release_info_from_archive_prefers_manifest_json(self):
        with TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "roblox.zapk"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "package_name": sync_roblox.PACKAGE_NAME,
                            "version_name": "2.711.876",
                            "version_code": "2036",
                            "split_apks": [{"id": "base", "file": "base.apk"}],
                        }
                    ),
                )
                archive.writestr("base.apk", b"placeholder")

            manifest_info = sync_roblox.discover_release_info_from_artifacts([archive_path])

            self.assertIsNotNone(manifest_info)
            self.assertEqual("2.711.876", manifest_info.version_name)
            self.assertEqual("2036", manifest_info.version_code)

    @mock.patch("sync_roblox.discover_release_info_from_artifacts")
    def test_reconcile_release_info_prefers_artifact_metadata(self, mock_discover):
        mock_discover.return_value = sync_roblox.ApkManifestInfo(
            package_name=sync_roblox.PACKAGE_NAME,
            version_name="2.711.876",
            version_code="2036",
            split_name=None,
        )
        original = sync_roblox.ReleaseInfo(
            version_name="2.711.876",
            version_code="20362",
            download_url="https://example.invalid/roblox.xapk",
            file_ext=".xapk",
        )

        reconciled = sync_roblox.reconcile_release_info_from_artifacts(
            original,
            [Path("roblox.zapk")],
        )

        self.assertEqual("2.711.876", reconciled.version_name)
        self.assertEqual("2036", reconciled.version_code)

    @mock.patch("sync_roblox.discover_release_info_from_artifacts")
    def test_reconcile_release_info_ignores_package_mismatch(self, mock_discover):
        mock_discover.return_value = sync_roblox.ApkManifestInfo(
            package_name="com.example.other",
            version_name="1.0.0",
            version_code="1",
            split_name=None,
        )
        original = sync_roblox.ReleaseInfo(
            version_name="2.711.876",
            version_code="20362",
            download_url="https://example.invalid/roblox.xapk",
            file_ext=".xapk",
        )

        reconciled = sync_roblox.reconcile_release_info_from_artifacts(
            original,
            [Path("roblox.zapk")],
        )

        self.assertEqual(original, reconciled)


if __name__ == "__main__":
    unittest.main()
