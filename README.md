# Android Backup Manager [alpha-v1.0.0]

A desktop GUI for backing up files from an Android device (via ADB) to a computer with incremental runs, filtering, and selective restore, built with Python and Qt for Python (PySide6).

> Note: This is an alpha pre-release intended for early evaluation and feedback before a stable v1.0.0.

## Table of Contents

- Overview
- Features
- Requirements
- Quick Start
- First-Run Setup
- Configuration
- Usage
- Data Layout and Storage
- Incremental Backup Logic
- Build and Releases
- CI and Versioning
- Troubleshooting
- Privacy and Security
- Contributing
- License
- Acknowledgements


## Overview

Android Backup Manager streamlines the process of discovering folders on an Android device and pulling files to a local destination, while maintaining a persistent record for incremental backups and a consolidated restore index for browsing and restoring specific files back to the device under the configured source root.

The GUI presents a Backup tab for discovery and copying and a Restore tab for browsing the merged file tree across dated sessions with selective restore support via ADB.

## Features

- GUI with Backup and Restore tabs built on PySide6 widgets for a responsive desktop experience.
- ADB-powered discovery and transfers implemented with a lightweight ADB client wrapper that hides console windows on Windows and exposes shell, push, and pull operations.
- Incremental backups using a JSON-backed record to skip files already copied in prior runs, ensuring efficient repeat sessions.
- Folder filtering via a simple JSON file with substring-based exclusions to avoid noisy or system-managed folders such as “Android”.
- Restore view that merges files from all backup dates into a single tree with roots listed per file, allowing selective push back to the device.
- Robust JSON writes using atomic file replacement to reduce the risk of truncated or corrupted record files on failure or power loss.
- Windows-friendly path mapping for local destinations that sanitizes illegal filename characters (e.g., colon) and preserves directory structure.


## Requirements

- Windows 10/11 recommended for packaged builds; Linux/macOS may work when running from source with Python and Qt dependencies installed.
- Python 3.10+ recommended for source runs and development workflows.
- Android device with Developer Options enabled and USB Debugging turned on to allow ADB communication and authorization.
- ADB (Android platform-tools) installed, with the absolute path to adb.exe configured during first-run setup or in app_config.json.
- USB data cable and appropriate device drivers on Windows to ensure reliable device detection and transfers.


## Quick Start

1) Install Python

- Install Python 3.10+ and ensure python and pip are available on PATH for source execution.

2) Install ADB

- Install platform-tools, note the path to adb.exe, and approve device authorization prompts when first connecting the device.

3) Prepare the project

- Clone or download the repository, optionally create a virtual environment, and install dependencies with pip as per your requirements file.

4) Connect the Android device

- Enable USB Debugging, connect via USB, and verify detection with the device status indicator in the app’s header.

5) Run the app from source

- From the repo root, start the GUI:
    - Code:
        - python main.py
The first-run wizard will appear to capture adb.exe, backup base folder, and default user.


## First-Run Setup

The First-Run Wizard asks for the ADB tool path, a writable base backup folder, and a default user name to scope backups and restore records, validating adb.exe with a short “adb version” probe.

These values are written to config/app_config.json next to the executable or script, and some UI preferences are persisted using application-scoped QSettings identifiers.

## Configuration

- Machine-level application configuration: config/app_config.json (written by the wizard or edited manually), containing ADB_PATH, SOURCE_DIR, BASE_BACKUP_DIR, and DEFAULT_USER keys.
- Folder filters: config/filters.json containing an “excluded_folders” array, used to filter device folders during discovery for “Backup All (Filtered)”.
- Runtime records (per user):
    - record.json stored at <BASE_BACKUP_DIR>/<USER>/record.json, tracking copied files to prevent re-copying.
    - restore_record.json stored at <BASE_BACKUP_DIR>/<USER>/restore_record.json, aggregating session files across dates for the Restore tree.

Example config/app_config.json:

```json
{
  "ADB_PATH": "D:/Softwares/android-tools/platform-tools-latest-windows/platform-tools/adb.exe",
  "SOURCE_DIR": "/sdcard/",
  "BASE_BACKUP_DIR": "D:/Mobile-Backup",
  "DEFAULT_USER": "User"
}
```

Example config/filters.json:

```json
{
  "excluded_folders": [
    ".SLOGAN",
    "Android"
  ]
}
```


## Usage[^12]

Backup

- Click Refresh Device to verify ADB connectivity, then click Scan Folders to list top-level device folders filtered by filters.json, and choose either Backup All (Filtered) or select specific folders and click Backup Selected, with progress feedback and a session log.

Restore

- In the Restore tab, browse the merged tree built from restore_record.json, select one or more leaf files via checkbox or selection, and click Restore Selected to push files back under SOURCE_DIR with automatic remote parent directory creation via adb shell mkdir -p.


## Data Layout and Storage

Local backup layout:

- <BASE_BACKUP_DIR>/<USER>/<YYYY-MM-DD>/<TopFolder>/<subpaths>/<file> reflecting device-relative paths under the chosen top-level folder with Windows-safe naming for the local filesystem.

Per-user records at the user root:

- record.json contains an “included_folders” array of device-relative paths like “Download/file.txt” to drive incremental skipping of previously copied files.
- restore_record.json contains a “roots” map keyed by date-like backup folders with a description and a files array of device-relative paths, used to construct the Restore tree.


## Incremental Backup Logic

- For each successfully pulled file, a normalized relative path is added to record.json, and subsequent runs skip any file whose relative path is already recorded to save time and bandwidth.
- Each session also updates restore_record.json by appending the session root name to the file’s entry, producing a reverse index of which dates contain a given relative path for convenient restore selection.


## Build and Releases

Run from source

- python main.py to launch the GUI from the project root in a Python environment with dependencies installed.

Standalone build (Windows)

- Use PyInstaller to package a single-file executable if desired for distribution, ensuring config and resource files ship alongside the binary or are generated on first-run.

Pre-release and stable distribution

- alpha-v1.0.0 artifacts will be produced by CI and attached to GitHub Releases for download and testing, with final v1.0.0 to follow after feedback and stabilization.


## CI and Versioning

Release cadence

- Pre-release channel: alpha-v1.0.0 with incremental tags to trigger pipeline builds and release uploads for early testers, followed by stable v1.0.0 when ready.

Pipeline overview

- On tag push (e.g., v1.0.0-alpha.0), the pipeline will build the Windows executable and upload the asset to the corresponding Release, alongside checks for launching the GUI and reading default configs.

Versioning approach

- Use SemVer for tags and user-facing release notes while ensuring internal module metadata and UIs remain aligned with the release train for consistency across code and artifacts.


## Troubleshooting

Device not detected

- Ensure the configured adb path is correct, the device is connected with USB Debugging enabled, and the authorization prompt is approved on first connection, then use Refresh Device to test.

Empty listings or permission errors

- Only shared/public storage (such as /sdcard) is accessible, and OEM restrictions may limit visibility of app-private directories, so rely on the configured SOURCE_DIR and filters to scope discovery.

Invalid characters or long paths on Windows

- The path mapper sanitizes characters like colon when mapping device filenames to local paths, but very deep nesting can still hit OS limits, so consider backing up selected folders for large media collections.

Restore folder creation failures

- The restore flow issues mkdir -p on the device before pushing files; if failures persist, confirm shell execution works and there is sufficient device storage and permissions.


## Privacy and Security

- All operations run locally via ADB, and the application writes only JSON records and logs to the local filesystem, with no network data transfers by default.
- Review the target paths and destination base directory when distributing packaged builds to ensure users understand where data is stored and how to remove it if needed.


## Contributing

- Issues and pull requests are welcome; keep GUI work responsive by using worker objects in threads and include concise docstrings and clear commit messages for code changes.
- Please test both discovery and backup flows with and without filters and ensure restore behaviors are consistent with the merged tree before submitting changes.


## License

- Include your preferred license file (e.g., MIT or Apache-2.0) at the repository root and update this section to match the selected license for the distribution.


## Acknowledgements

- Android platform-tools (ADB) for device communication and file transfer capabilities from the host machine.
- Qt for Python (PySide6) for building the GUI including the Backup and Restore experiences and threaded worker patterns used throughout the app.
