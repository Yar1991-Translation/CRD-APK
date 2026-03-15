import unittest
import xml.etree.ElementTree as ET
import zipfile

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import mergeapks


ANDROID_NAME = mergeapks.const_android_attr_prefix + "name"
ANDROID_VALUE = mergeapks.const_android_attr_prefix + "value"


class MergeApksTests(unittest.TestCase):
    def test_update_main_manifest_file_removes_split_markers(self):
        manifest_text = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app"
    android:isSplitRequired="true"
    android:requiredSplitTypes="base__abi"
    android:splitTypes="">
    <uses-split android:name="config.arm64_v8a" />
    <application>
        <meta-data android:name="com.android.vending.splits.required" android:value="true" />
        <meta-data android:name="com.android.vending.splits" android:resource="@xml/splits0" />
        <meta-data android:name="com.android.stamp.type" android:value="STAMP_TYPE_DISTRIBUTION_APK" />
        <meta-data android:name="keep.me" android:value="1" />
    </application>
</manifest>
"""

        with TemporaryDirectory() as temp_dir:
            apk_dir = Path(temp_dir)
            manifest_path = apk_dir / "AndroidManifest.xml"
            manifest_path.write_text(manifest_text, encoding="utf-8")

            mergeapks.update_main_manifest_file(str(apk_dir))

            manifest_root = ET.parse(manifest_path).getroot()
            self.assertNotIn(
                mergeapks.const_android_attr_prefix + "isSplitRequired",
                manifest_root.attrib,
            )
            self.assertNotIn(
                mergeapks.const_android_attr_prefix + "requiredSplitTypes",
                manifest_root.attrib,
            )
            self.assertNotIn(
                mergeapks.const_android_attr_prefix + "splitTypes",
                manifest_root.attrib,
            )
            self.assertEqual([], manifest_root.findall("uses-split"))

            application = manifest_root.find("application")
            meta_by_name = {
                element.attrib.get(ANDROID_NAME): element
                for element in application.findall("meta-data")
            }
            self.assertNotIn("com.android.vending.splits.required", meta_by_name)
            self.assertNotIn("com.android.vending.splits", meta_by_name)
            self.assertEqual("1", meta_by_name["keep.me"].attrib[ANDROID_VALUE])
            self.assertEqual(
                "STAMP_TYPE_STANDALONE_APK",
                meta_by_name["com.android.stamp.type"].attrib[ANDROID_VALUE],
            )

    def test_delete_split_related_files_removes_bundletool_leftovers(self):
        with TemporaryDirectory() as temp_dir:
            apk_dir = Path(temp_dir)
            (apk_dir / "res" / "xml").mkdir(parents=True)
            (apk_dir / "res" / "values").mkdir(parents=True)
            (apk_dir / "res" / "xml" / "splits0.xml").write_text("<resources/>", encoding="utf-8")
            (apk_dir / "res" / "xml" / "network_security_config.xml").write_text(
                "<resources/>",
                encoding="utf-8",
            )
            (apk_dir / "res" / "values" / "public.xml").write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <public type="xml" name="splits0" id="0x7f15000a" />
    <public type="xml" name="network_security_config" id="0x7f15000b" />
</resources>
""",
                encoding="utf-8",
            )
            (apk_dir / "original" / "META-INF").mkdir(parents=True)
            (apk_dir / "unknown" / "META-INF").mkdir(parents=True)
            (apk_dir / "original" / "META-INF" / "BNDLTOOL.RSA").write_text("x", encoding="utf-8")
            (apk_dir / "unknown" / "META-INF" / "BNDLTOOL.SF").write_text("x", encoding="utf-8")
            (apk_dir / "unknown" / "stamp-cert-sha256").write_text("x", encoding="utf-8")

            mergeapks.delete_signature_related_files(str(apk_dir))
            mergeapks.delete_split_related_files(str(apk_dir))

            self.assertFalse((apk_dir / "res" / "xml" / "splits0.xml").exists())
            self.assertTrue((apk_dir / "res" / "xml" / "network_security_config.xml").exists())
            self.assertFalse((apk_dir / "original" / "META-INF" / "BNDLTOOL.RSA").exists())
            self.assertFalse((apk_dir / "unknown" / "META-INF" / "BNDLTOOL.SF").exists())
            self.assertFalse((apk_dir / "unknown" / "stamp-cert-sha256").exists())
            public_xml = (apk_dir / "res" / "values" / "public.xml").read_text(encoding="utf-8")
            self.assertNotIn('name="splits0"', public_xml)
            self.assertIn('name="network_security_config"', public_xml)

    @mock.patch("mergeapks.read_binary_manifest_root")
    def test_verify_merged_apk_rejects_split_markers(self, mock_manifest_reader):
        mock_manifest_reader.return_value = ET.fromstring(
            """<manifest xmlns:android="http://schemas.android.com/apk/res/android"
            package="com.example.app"
            android:isSplitRequired="true">
            <application>
                <meta-data android:name="com.android.vending.splits.required" android:value="true" />
            </application>
            </manifest>"""
        )

        with TemporaryDirectory() as temp_dir:
            apk_path = Path(temp_dir) / "broken.apk"
            with zipfile.ZipFile(apk_path, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("res/xml/splits0.xml", "<resources/>")

            with self.assertRaisesRegex(Exception, "split install markers"):
                mergeapks.verify_merged_apk(apk_path)

    @mock.patch("mergeapks.read_binary_manifest_root")
    def test_verify_merged_apk_accepts_clean_output(self, mock_manifest_reader):
        mock_manifest_reader.return_value = ET.fromstring(
            """<manifest xmlns:android="http://schemas.android.com/apk/res/android"
            package="com.example.app">
            <application>
                <meta-data android:name="keep.me" android:value="1" />
            </application>
            </manifest>"""
        )

        with TemporaryDirectory() as temp_dir:
            apk_path = Path(temp_dir) / "clean.apk"
            with zipfile.ZipFile(apk_path, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("classes.dex", b"dex")

            mergeapks.verify_merged_apk(apk_path)


if __name__ == "__main__":
    unittest.main()
