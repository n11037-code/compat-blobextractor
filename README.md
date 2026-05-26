# compat-blobextractor
compat blobextractor: a firmware blob extractor for google pixel devices.

# CompatOS Firmware Blob Extractor

A portable Python utility for extracting proprietary firmware blobs from Android factory images. Designed for mobile Linux projects (CompatOS, PostmarketOS, Mobian, etc.) that need device firmware to support hardware like WiFi, modem, Bluetooth, and GPU on real devices.

---

##  Legal Disclaimer

**Firmware blobs extracted by this tool are proprietary binaries.** They are owned by their respective manufacturers (Google, Qualcomm, Broadcom, Arm, MediaTek, etc.) and are subject to their own licence terms.

- This tool does not distribute firmware. It only automates extraction from factory images you already possess.
- You may only use extracted firmware on devices you own or are legally authorised to operate.
- Do not redistribute extracted firmware blobs. Direct others to extract their own from official factory images.
- The tool authors accept no liability for misuse of extracted firmware.

---

##  AI Assistance Disclaimer

Portions of this tool were developed with the assistance of AI (Claude by Anthropic). The code has been reviewed, tested, and validated by the project maintainer across multiple real device factory images.
---

## Features

- **Auto-detection:** drop a factory `.zip` into `input/` and run.
- **Multi-strategy extraction:** tries `7z` first, falls back to `simg2img` conversion for Android sparse images.
- **Wide pattern coverage:** catches `.bin`, `.fw`, `.mbn`, `.mdt`, `.hcd`, `.cal`, `.blob`, `.conf`, extensionless blobs, and more.
- **GPU dedicated pass:** separate scan for Mali, Adreno, and PowerVR firmware and userspace drivers.
- **Category coverage table:** — device-agnostic check showing whether GPU, WiFi, Modem, Bluetooth, Audio, NFC, Camera, and Security firmware was found.
- **Manifest output:** writes `blob_manifest.json` and `blob_summary.txt` to the output folder after every run.

---

## Requirements

**Python:** 3.8 or newer. The script will exit with a clear error if your version is too old.

**System packages** (Ubuntu/Debian):
```bash
sudo apt install p7zip-full android-sdk-libsparse-utils
```

**Python packages:**
```bash
pip3 install rich --break-system-packages
```

The script will detect missing dependencies on startup and offer to install them automatically. If auto-install fails, use the commands above manually.

---

## Usage

### 1. Get a factory image

For **Google Pixel** devices, download the official factory image zip from:
**https://developers.google.com/android/images**

For **Xiaomi/POCO** fastboot ROMs, community mirrors such as `xiaomifirmwareupdater.com` carry official builds.

For **Motorola, Nokia, and most other manufacturers** — see [Limitations](#limitations). These devices use a different image format that this tool does not currently support.

### 2. Place the zip in input/

```
blobextractor/
└── blobextractor_workspace/
    └── input/
        └── oriole-bp1a.250505.005-factory-9fc37bcc.zip   ← place here
```

### 3. Execute blobextractor inside working directory.

```bash
python3 blobextractor.py
```

### 4. Collect output

Blobs are extracted to `blobextractor_workspace/output/<codename>/`.

---

## Tested Devices

| Device             | Codename  | SoC               | GPU        | Result                                       |
|--------------------|-----------|-------------------|------------|----------------------------------------------|
| Google Pixel 3a    | sargo     | Snapdragon 670    | Adreno 615 |  Full extraction                             |
| Google Pixel 6     | oriole    | Tensor G1 (GS101) | Mali-G78   |  Full extraction, GPU as `g6.app`            |
| Google Pixel 9 Pro | caiman    | Tensor G4         | Mali-G715  |  Full extraction, multiple Mali CSF versions |

---

## Output Structure

The output folder preserves the original vendor partition directory structure:

```
output/<codename>/
├── firmware/          ← kernel firmware blobs (/lib/firmware on device)
├── lib/egl/           ← 32-bit GPU userspace drivers
├── lib64/egl/         ← 64-bit GPU userspace drivers
├── lib/camera/        ← camera tuning blobs (Qualcomm devices)
├── etc/               ← config files (wifi, bt, nfc, modem, gnss, uwb)
├── blob_manifest.json ← machine-readable extraction log
└── blob_summary.txt   ← human-readable summary
```

---

## Category Coverage Table

At the end of each run the tool prints a device-agnostic coverage table. Rather than checking for specific filenames, it asks whether *anything* matching each firmware category was found:

```
Firmware Category Coverage
┌──────────────────┬─────────────┬─────────────────────────────┐
│ Category         │ Status      │ Example match               │
├──────────────────┼─────────────┼─────────────────────────────┤
│ GPU              │   Covered   │ mali_csffw-r53p0.bin        │
│ WiFi             │   Covered   │ fw_bcmdhd.bin               │
│ Modem            │   Covered   │ modem.bin                   │
│ Bluetooth        │   Covered   │ BCM.hcd                     │
│ Audio            │   Covered   │ cs35l41-dsp1-spk-prot.bin   │
│ NFC              │   Covered   │ st54l_fw.bin                │
│ Camera           │   Not found │                             │
│ Citadel/Security │   Covered   │ evt.ec.bin                  │
└──────────────────┴─────────────┴─────────────────────────────┘
```

A "Not found" result does not necessarily mean the firmware is absent — it may use a naming convention not yet covered by the tool's patterns.

---

## Compatibility

### Supported platforms

| Platform                      | Status          | Notes                                                                                                      |
|-------------------------------|-----------------|------------------------------------------------------------------------------------------------------------|
| Ubuntu 22.04 / 24.04 (native) | Fully supported | Primary target                                                                                             |
| WSL2 on Windows               | Fully supported | Tested environment                                                                                         |
| Other Debian-based Linux      | Should work     | Manual dependency install may be needed                                                                    |
| Fedora / Arch / other Linux   | Partial         | Extraction works; auto-install uses `apt-get` and will fail — install `p7zip` and `android-tools` manually |
| macOS                         | Not supported   | `simg2img` unavailable; `apt-get` absent; path handling untested                                           |
| Windows (native)              | Not supported   | No `7z` or `simg2img` in PATH; requires WSL2                                                               |

### Supported image formats

| Format                                        | Status          | Notes                                                                                      |
|-----------------------------------------------|-----------------|--------------------------------------------------------------------------------------------|
| Google factory image zip                      | Fully supported | Tested on Pixel 3a, 6, 9 Pro                                                               |
| Qualcomm fastboot zip (Xiaomi, OnePlus older) | Should work     | Similar structure to Pixel                                                                 |
| Android sparse `.img`                         | Supported       | Via `simg2img` fallback                                                                    |
| EROFS filesystem images                       | Partial         | Newer 7z versions can read some; `system.img` and `vendor_boot.img` are typically skipped  |
| `payload.bin` OTA format                      | Not supported   | Used by Motorola, Nokia, newer OnePlus — see below                                         |
| Samsung Odin `.tar.md5`                       | Not supported   | Completely different format; would need a separate code path                               | 

---

## Limitations

**`payload.bin` OTA format not supported**
Motorola, Nokia, and some OnePlus devices ship OTA zips containing a single `payload.bin` using the Android A/B format rather than individual `.img` files. This tool cannot process them. A future version may add `payload-dumper-go` integration as a pre-processing step. This affects the `manaus` (Motorola Edge 40 Neo) MoxieOS target.

**EROFS images partially skipped**
Newer Pixel images use EROFS filesystem for `system.img`, `vendor_boot.img`, and others. These are listed as skipped in the output. The blobs that matter most (from `vendor.img`) are typically ext4 and extract fine. Loop-mount extraction of EROFS would require root and is not yet implemented.

**Duplicate entries in blob table**
Blobs that match both the standard firmware patterns and the GPU-specific pass are currently logged and listed twice. This inflates the blob count in the summary. The files themselves are not duplicated on disk — only the log entry is affected.

**Camera category often shows Not found on Tensor devices**
Tensor-based Pixels (oriole, caiman) store camera tuning data differently from Qualcomm devices. The patterns cover Qualcomm camera blobs well but miss some Tensor-specific paths.

**Multiple zips in input/ on WSL2**
If more than one zip is present in `input/`, the tool picks the newest by file modification time. On WSL2, Windows file timestamps are sometimes unreliable and the wrong zip may be selected. Keep only one zip in `input/` at a time to avoid this.

**Silent copy failures**
If a blob copy fails due to permissions or disk space, the tool currently moves on without a visible warning. Check `blob_manifest.json` after a run if you suspect missing files.

**No ADB extraction**
Live device extraction via ADB is not implemented. This tool works from factory images only.

**Auto-install requires re-run**
If dependencies are installed by the auto-installer, the script must be run a second time. This is intentional — Python cannot reload newly installed modules mid-run.

---

## Known Issues

| Issue                                           | Impact                           | Status                               |
|-------------------------------------------------|----------------------------------|--------------------------------------|
| Duplicate blob log entries for GPU blobs        | Inflated count in summary        | Known, low priority                  |
| `apt-get` auto-install only; no `dnf`/`pacman`  | Breaks on non-Debian distros     | Known                                |
| Camera category misses Tensor devices           | False negative in coverage table | Known                                |
| WSL2 timestamp unreliability with multiple zips | May pick wrong zip               | Known, workaround: one zip at a time |

---

## Directory Structure

```
blobextractor/
├── blobextractor.py              # Main script
├── README.md                     # This file
└── blobextractor_workspace/
    ├── input/                    # Place factory zip here
    ├── temp/                     # Temporary extraction (auto-wiped)
    └── output/
        ├── oriole/               # Per-device blob output
        ├── sargo/
        └── caiman/
```
---

## Adding a New Device Codename

The tool auto-detects codenames from the zip filename using the Google naming convention (`<codename>-<buildid>-factory-<hash>.zip`). If your device is not in the known list, it falls back to the first hyphen-delimited segment of the filename automatically, no code change needed in most cases.

To add a codename explicitly, open `blobextractor.py` and add the string to `KNOWN_CODENAMES` near the top of the file.

---

## Licence

The tool code is released under the **MIT Licence**. See `LICENSE` for details.

Extracted firmware blobs are **not** covered by this licence and remain the property of their respective owners.
