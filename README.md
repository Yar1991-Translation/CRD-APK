# RB-APK

This repository automatically monitors the latest Roblox Android package (`com.roblox.client`), downloads the newest artifact from APKPure, merges split APKs into a signed universal-style APK with `apktool`, and publishes `roblox.zapk` plus `roblox.apk` to GitHub Releases.

## Files

- `sync_roblox.py`: Checks the latest version, downloads the artifact, normalizes split APK output when needed, calculates SHA256, and exports GitHub Actions outputs.
- `mergeapks.py`: Uses `apktool`, `zipalign`, and `apksigner` to merge split APK files into one signed APK.
- `scripts/`: Small helper installers and release-note generators used by GitHub Actions.
- `.github/workflows/release.yml`: Runs daily or on demand, installs dependencies and Android tooling on the runner, renames release assets to `roblox.zapk` and `roblox.apk`, commits `latest_version.txt`, and creates the GitHub Release.
- `latest_version.txt`: Stores the last released Roblox Android version.

## How it works

1. Query APKPure metadata for the newest Roblox Android build.
2. Compare it with `latest_version.txt`.
3. Exit cleanly when there is no update.
4. Download the newest `.apk` or `.xapk`.
5. Package split APKs into `.xapk` when needed.
6. In GitHub Actions, install `apktool` and Android build-tools, then merge split APKs into a signed APK.
7. Rename the downloaded split archive to `roblox.zapk` and the merged APK to `roblox.apk`.
8. Generate SHA256 and release notes.
9. Commit the updated version file and publish a GitHub Release tagged with the downloaded version number.

## Archive conversion

The script also supports converting existing `.apk`, `.xapk`, or `.apks` archives:

```bash
python sync_roblox.py --convert-archive dist/roblox-android-v2.711.876.xapk
```

Outputs are written to `converted-apks/` by default. When using `--convert-method apktool`, outputs go to `merged-apks/` by default.

- Single-package archives are exported directly as a normal `.apk`.
- Split-package archives export the selected base APK as `<name>.apk` and also extract all split APK files into a companion directory.

For split APK packages, `--convert-method extract` keeps the full split set alongside the extracted base APK. `--convert-method apktool` runs the merge flow used by the repository's GitHub Actions workflow and produces a signed merged APK.
