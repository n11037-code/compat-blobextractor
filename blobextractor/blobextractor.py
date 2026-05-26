import os
import shutil
import subprocess
import zipfile
import json
import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, MofNCompleteColumn
from rich.table import Table
from rich import box

console = Console()

# --- CONFIGURATION ---
SCRIPT_DIR = Path(__file__).parent.absolute()
WORKSPACE  = SCRIPT_DIR / "blobextractor_workspace"
INPUT_DIR  = WORKSPACE / "input"
EXTRACT_DIR= WORKSPACE / "temp"
OUTPUT_BASE = WORKSPACE / "output"

# Set after codename detection in main()
OUTPUT_DIR = OUTPUT_BASE

# Firmware file patterns — covers Android, Qualcomm, MediaTek, Broadcom, Mali, misc
FIRMWARE_PATTERNS = [
    "*.bin", "*.fw", "*.est", "*.mdlt", "*.tlm", "*.srec",
    "*.mbn", "*.mdt", "*.fbn", "*.img2", "*.zst", "*.lz4",
    "*.hcd", "*.nvm", "*.cal", "*.blob", "*.conf",
    "*.fw2", "*.fw3", "*.pnvm", "*.ucode",
]

# Extensionless blob names to hunt for explicitly
EXTENSIONLESS_BLOBS = [
    "mali_csffw", "mali_csffw.bin",
    "a660_sqe", "a660_sqe.fw",
    "qcdxkmsuc", "modemuw", "modem_fw",
    "WCNSS_qcom_wlan_nv", "WCNSS_cfg",
    "bdwlan", "Data.msc",
]

# GPU-specific search paths within extracted images
GPU_SEARCH_PATHS = [
    "**/firmware/mali*",
    "**/firmware/g[0-9]*",
    "**/firmware/sgx*",
    "**/firmware/pvr*",
    "**/firmware/a[0-9][0-9][0-9]*",   # Adreno (e.g. a660_sqe)
    "**/egl/**",
    "**/firmware/adreno*",
]

IMAGE_TYPES = {
    "vendor_dlkm": "Vendor Dynamic Kernel Modules (GPU blobs often here)",
    "vendor":      "Vendor Partition (Primary firmware source)",
    "system":      "System OS Partition",
    "product":     "Product Partition",
    "boot":        "Boot/Kernel Image",
    "radio":       "Modem/Radio Firmware",
    "modem":       "Modem Firmware",
    "dtbo":        "Device Tree Blob Overlay",
    "vbmeta":      "Verified Boot Metadata",
    "super":       "Dynamic Partition Container",
    "bootloader":  "Bootloader Image",
}

# Tracks results for manifest
extraction_log = {
    "timestamp": "",
    "source_zip": "",
    "blobs": [],          # {file, source_image, rel_path}
    "skipped_images": [], # images 7z+simg2img both failed on
    "missing_critical": [],
}

# Category-based firmware coverage check — pattern matched against found filenames.
# Each category has a list of fnmatch patterns. If ANY pattern matches ANY found
# blob, the category is considered covered. Device-agnostic by design.
FIRMWARE_CATEGORIES = {
    "GPU":       ["*mali*", "*adreno*", "*g[0-9]*.app", "*a6[0-9][0-9]*sqe*",
                  "*a7[0-9][0-9]*sqe*", "*sgx*", "*pvr*", "*libGLES*", "*egl*",
                  "*libEGL*", "*libGLESv*"],
    "WiFi":      ["*wlan*", "*wifi*", "*bcmdhd*", "*bdwlan*", "*fw_bcm*",
                  "*wlanmdsp*", "*WCNSS*"],
    "Modem":     ["*modem*", "*radio*", "*ril*", "*mpss*", "*adsp*",
                  "mcfg_sw*", "mcfg_hw*"],
    "Bluetooth": ["*.hcd", "*BCM*.hcd", "*BTFW*", "*bt_fw*", "*btfw*",
                  "*brcm*bt*"],
    "Audio":     ["*cs35l*", "*cs40l*", "*drv262*", "*rt5514*", "*crus*",
                  "*audio*dsp*", "*adsp*.bin"],
    "NFC":       ["*pn5*", "*st54*", "*nfc*"],
    "Camera":    ["*imx*", "*fdconfig*", "*camera*tuned*"],
    "Citadel/Security": ["*citadel*", "*ec.bin", "*dauntless*", "*fip.bin",
                         "*widevine*", "*keymaster*"],
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def run_cmd(cmd, shell=False):
    try:
        result = subprocess.run(
            cmd, shell=shell, check=True,
            capture_output=True, text=True
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return None


def check_python_version():
    import sys
    if sys.version_info < (3, 8):
        print(f"ERROR: Python 3.8+ required. You have {sys.version}")
        sys.exit(1)


def check_dependencies():
    console.print("\n[bold yellow]Checking system dependencies...[/bold yellow]")

    # System tools — installable via apt
    system_required = {
        "7z":       "p7zip-full",
        "simg2img": "android-sdk-libsparse-utils",
    }
    missing_apt = [pkg for tool, pkg in system_required.items()
                   if shutil.which(tool) is None]

    # Python packages — installable via pip
    missing_pip = []
    try:
        import rich  # noqa
    except ImportError:
        missing_pip.append("rich")

    if not missing_apt and not missing_pip:
        console.print("[green]✓ All dependencies met.[/green]")
        return True

    if missing_apt:
        console.print(f"[red]Missing system packages: {', '.join(missing_apt)}[/red]")
        console.print(f"[dim]Install with: sudo apt-get install -y {' '.join(missing_apt)}[/dim]")
    if missing_pip:
        console.print(f"[red]Missing Python packages: {', '.join(missing_pip)}[/red]")
        console.print(f"[dim]Install with: pip3 install {' '.join(missing_pip)} --break-system-packages[/dim]")

    if Confirm.ask("\nAttempt automatic installation now?"):
        success = True
        if missing_apt:
            console.print("[dim]Installing system packages...[/dim]")
            result = subprocess.run(
                ["sudo", "apt-get", "install", "-y"] + missing_apt
            )
            if result.returncode != 0:
                console.print("[red]✘ apt-get install failed. Install manually and re-run.[/red]")
                success = False
        if missing_pip:
            console.print("[dim]Installing Python packages...[/dim]")
            result = subprocess.run(
                ["pip3", "install", "--break-system-packages"] + missing_pip
            )
            if result.returncode != 0:
                console.print("[red]✘ pip3 install failed. Install manually and re-run.[/red]")
                success = False
        if success:
            console.print("[green]✓ Dependencies installed. Re-run the script.[/green]")
        return False  # Always re-run after install so imports are fresh
    return False


def setup_environment():
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)


def get_image_type(img_path):
    name = img_path.name.lower()
    for key, label in IMAGE_TYPES.items():
        if key in name:
            return label
    return "Unknown Image Type"


# ---------------------------------------------------------------------------
# Device codename detection
# ---------------------------------------------------------------------------

# Known device codenames — add more here as needed
KNOWN_CODENAMES = [
    # Google Pixel
    "oriole", "raven",           # Pixel 6, 6 Pro
    "bluejay",                   # Pixel 6a
    "cheetah", "panther",        # Pixel 7, 7a
    "lynx",                      # Pixel 7a
    "husky", "shiba",            # Pixel 8, 8 Pro
    "akita", "caiman",           # Pixel 9, 9 Pro
    "sargo", "bonito",           # Pixel 3a, 3a XL
    "flame", "coral",            # Pixel 4, 4 XL
    "sunfish",                   # Pixel 4a
    "redfin", "bramble",         # Pixel 5, 5a
    "barbet",                    # Pixel 5a
    # Motorola
    "manaus",                    # Edge 40 Neo
    # add more as needed
]

def detect_codename(zip_name):
    """
    Extract device codename from factory zip filename.
    Google naming: <codename>-<buildid>-factory-<hash>.zip
    Falls back to the first segment of the filename.
    """
    stem = Path(zip_name).stem.lower()
    # Check against known list first
    for codename in KNOWN_CODENAMES:
        if stem.startswith(codename):
            return codename
    # Fallback: first hyphen-delimited segment
    first_segment = stem.split("-")[0]
    if first_segment:
        return first_segment
    return "unknown_device"


# ---------------------------------------------------------------------------
# Auto-detect factory zip in input/
# ---------------------------------------------------------------------------

def find_input_zip():
    zips = list(INPUT_DIR.glob("*.zip"))
    if not zips:
        return None
    if len(zips) == 1:
        console.print(f"[green]✓ Auto-detected factory image:[/green] [white]{zips[0].name}[/white]")
        return zips[0]
    # Multiple zips — pick newest
    zips.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    console.print(f"[yellow]Multiple zips found — using newest:[/yellow] [white]{zips[0].name}[/white]")
    for z in zips[1:]:
        console.print(f"  [dim]Ignored: {z.name}[/dim]")
    return zips[0]


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_zip_file(zip_path, extract_path):
    """Unzip with a real progress bar based on member count."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            members = zf.infolist()
            total = len(members)
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                console=console
            ) as progress:
                task = progress.add_task(f"Unzipping [cyan]{zip_path.name}[/cyan]", total=total)
                for member in members:
                    zf.extract(member, extract_path)
                    progress.advance(task)
        return True
    except Exception as e:
        console.print(f"[red]Error unzipping {zip_path.name}: {e}[/red]")
        return False


def try_extract_image(img_path, dest_dir):
    """
    Try to extract an .img file. Strategy:
    1. 7z directly (works on ext4, erofs if 7z supports it, zip-wrapped imgs)
    2. simg2img conversion then 7z (handles Android sparse format)
    3. Mount via loop device as last resort
    Returns (success: bool, method_used: str)
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Strategy 1: direct 7z
    res = run_cmd(["7z", "x", str(img_path), f"-o{dest_dir}", "-r", "-y"])
    if res is not None:
        return True, "7z"

    # Strategy 2: simg2img + 7z
    raw_path = dest_dir.parent / f"{img_path.stem}_raw.img"
    conv = run_cmd(["simg2img", str(img_path), str(raw_path)])
    if conv is not None and raw_path.exists():
        res2 = run_cmd(["7z", "x", str(raw_path), f"-o{dest_dir}", "-r", "-y"])
        raw_path.unlink(missing_ok=True)
        if res2 is not None:
            return True, "simg2img+7z"

    return False, "failed"


def copy_blob(blob_path, source_img_name, base_extract_dir):
    """Copy a found blob to OUTPUT_DIR, preserving relative path. Log it."""
    try:
        rel_path = blob_path.relative_to(base_extract_dir)
        dest = OUTPUT_DIR / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(blob_path, dest)
        extraction_log["blobs"].append({
            "file": blob_path.name,
            "source_image": source_img_name,
            "output_path": str(rel_path),
        })
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_images():
    # Unpack any nested zips first
    nested_zips = list(EXTRACT_DIR.rglob("*.zip"))
    if nested_zips:
        console.print(f"\n[cyan]📦 Found {len(nested_zips)} nested archive(s) — unpacking...[/cyan]")
        for inner_zip in nested_zips:
            console.print(f"  [dim]{inner_zip.name}[/dim]")
            extract_zip_file(inner_zip, EXTRACT_DIR)

    images = list(EXTRACT_DIR.rglob("*.img"))
    if not images:
        console.print("\n[red]❌ No .img files found after unpacking.[/red]")
        return

    # Sort: vendor first (most likely source), then vendor_dlkm, then rest
    def img_priority(p):
        n = p.name.lower()
        if "vendor_dlkm" in n: return 0
        if n == "vendor.img":  return 1
        if "vendor" in n:      return 2
        if "modem" in n:       return 3
        if "radio" in n:       return 4
        return 5
    images.sort(key=img_priority)

    console.print(f"\n[bold green]🔍 Found {len(images)} image(s) to process.[/bold green]\n")

    total_blobs = 0

    for img in images:
        img_type = get_image_type(img)
        console.rule(f"[bold yellow]{img.name}[/bold yellow]  [dim]{img_type}[/dim]")

        img_extract_dir = EXTRACT_DIR / f"x_{img.stem}"
        success, method = try_extract_image(img, img_extract_dir)

        if not success:
            console.print(f"  [dim]⚠ Could not extract (tried 7z + simg2img) — skipping.[/dim]")
            extraction_log["skipped_images"].append(img.name)
            continue

        console.print(f"  [dim]Extracted via {method}[/dim]")

        # Standard firmware pattern scan
        found_this_img = 0
        for pattern in FIRMWARE_PATTERNS:
            for blob in img_extract_dir.rglob(pattern):
                if blob.is_file():
                    if copy_blob(blob, img.name, img_extract_dir):
                        rel = blob.relative_to(img_extract_dir)
                        console.print(f"  [green]→[/green] [white]{rel}[/white]")
                        found_this_img += 1

        # Extensionless blob scan
        for name in EXTENSIONLESS_BLOBS:
            for match in img_extract_dir.rglob(name):
                if match.is_file():
                    if copy_blob(match, img.name, img_extract_dir):
                        rel = match.relative_to(img_extract_dir)
                        console.print(f"  [green]→[/green] [white]{rel}[/white] [dim](extensionless)[/dim]")
                        found_this_img += 1

        # GPU-specific deep scan
        gpu_found = 0
        for gpu_pattern in GPU_SEARCH_PATHS:
            for match in img_extract_dir.glob(gpu_pattern):
                if match.is_file():
                    if copy_blob(match, img.name, img_extract_dir):
                        rel = match.relative_to(img_extract_dir)
                        console.print(f"  [magenta]→ GPU:[/magenta] [white]{rel}[/white]")
                        found_this_img += 1
                        gpu_found += 1

        if found_this_img == 0:
            console.print("  [dim]No blobs found in this image.[/dim]")
        else:
            gpu_note = f"  [dim]({gpu_found} GPU)[/dim]" if gpu_found else ""
            console.print(f"  [bold green]✔ {found_this_img} blob(s) pulled.{gpu_note}[/bold green]")

        total_blobs += found_this_img
        shutil.rmtree(img_extract_dir, ignore_errors=True)

    return total_blobs


def write_manifest(source_zip_name):
    """Write a JSON manifest and a human-readable summary to output/."""
    extraction_log["timestamp"] = datetime.datetime.now().isoformat()
    extraction_log["source_zip"] = source_zip_name

    # Category coverage check
    found_names = {b["file"].lower() for b in extraction_log["blobs"]}
    found_paths = {b["output_path"].lower() for b in extraction_log["blobs"]}
    all_found = found_names | found_paths

    import fnmatch
    category_results = {}
    for category, patterns in FIRMWARE_CATEGORIES.items():
        matched = [
            f for f in all_found
            if any(fnmatch.fnmatch(f.split("/")[-1], p) or fnmatch.fnmatch(f, p)
                   for p in patterns)
        ]
        category_results[category] = matched
    extraction_log["category_coverage"] = {
        k: bool(v) for k, v in category_results.items()
    }
    extraction_log["missing_critical"] = [
        k for k, v in category_results.items() if not v
    ]

    manifest_path = OUTPUT_DIR / "blob_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(extraction_log, f, indent=2)

    summary_path = OUTPUT_DIR / "blob_summary.txt"
    with open(summary_path, "w") as f:
        f.write(f"MoxieOS Blob Extraction Summary\n")
        f.write(f"================================\n")
        f.write(f"Date:   {extraction_log['timestamp']}\n")
        f.write(f"Source: {source_zip_name}\n\n")
        f.write(f"Total blobs extracted: {len(extraction_log['blobs'])}\n\n")
        f.write("BLOBS:\n")
        for b in extraction_log["blobs"]:
            f.write(f"  {b['output_path']}  (from {b['source_image']})\n")
        if extraction_log["skipped_images"]:
            f.write("\nSKIPPED IMAGES (unreadable):\n")
            for s in extraction_log["skipped_images"]:
                f.write(f"  {s}\n")
        f.write("\nFIRMWARE CATEGORY COVERAGE:\n")
        for cat, covered in extraction_log.get("category_coverage", {}).items():
            status = "✓" if covered else "!!"
            f.write(f"  {status} {cat}\n")


def print_summary():
    """Print a Rich table summary at the end."""
    console.print()

    # Blobs table
    table = Table(title="Extracted Blobs", box=box.ROUNDED, show_lines=False)
    table.add_column("File", style="white")
    table.add_column("Source Image", style="dim")
    table.add_column("Output Path", style="cyan")
    for b in extraction_log["blobs"]:
        table.add_row(b["file"], b["source_image"], b["output_path"])
    console.print(table)

    # Category coverage table
    console.print()
    crit_table = Table(title="Firmware Category Coverage", box=box.ROUNDED)
    crit_table.add_column("Category")
    crit_table.add_column("Status")
    crit_table.add_column("Example match", style="dim")

    import fnmatch
    found_names_lower = {b["file"].lower() for b in extraction_log["blobs"]}
    found_paths_lower = {b["output_path"].lower() for b in extraction_log["blobs"]}
    all_found_lower = found_names_lower | found_paths_lower

    for category, patterns in FIRMWARE_CATEGORIES.items():
        matches = [
            f.split("/")[-1] for f in all_found_lower
            if any(fnmatch.fnmatch(f.split("/")[-1], p) or fnmatch.fnmatch(f, p)
                   for p in patterns)
        ]
        if matches:
            example = matches[0]
            if len(matches) > 1:
                example += f" (+{len(matches)-1} more)"
            crit_table.add_row(category, "[green]✓ Covered[/green]", example)
        else:
            crit_table.add_row(category, "[red]✘ Not found[/red]", "")
    console.print(crit_table)

    if extraction_log["skipped_images"]:
        console.print(f"\n[yellow]⚠ {len(extraction_log['skipped_images'])} image(s) could not be read:[/yellow]")
        for s in extraction_log["skipped_images"]:
            console.print(f"  [dim]{s}[/dim]")

    console.print(f"\n[bold green]Output:[/bold green] [white]{OUTPUT_DIR}[/white]")
    console.print(f"[bold green]Manifest:[/bold green] [white]{OUTPUT_DIR / 'blob_manifest.json'}[/white]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    check_python_version()
    console.print(Panel.fit(
        "MoxieOS Firmware Blob Extractor v0.9\n[dim]Drop factory image into input/ and run[/dim]",
        style="bold magenta"
    ))

    if not check_dependencies():
        return

    setup_environment()

    # Auto-detect zip in input/, no prompt needed
    factory_zip = find_input_zip()
    if factory_zip is None:
        console.print(f"\n[red]No factory .zip found in input/[/red]")
        console.print(f"[dim]Place your factory image zip in:[/dim] [white]{INPUT_DIR}[/white]")
        console.print("[dim]Then re-run this script.[/dim]")
        return

    codename = detect_codename(factory_zip.name)
    global OUTPUT_DIR
    OUTPUT_DIR = OUTPUT_BASE / codename
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold]Device:[/bold]  [cyan]{codename}[/cyan]")
    console.print(f"[bold]Source:[/bold]  {factory_zip}")
    console.print(f"[bold]Output:[/bold]  {OUTPUT_DIR}\n")

    try:
        if extract_zip_file(factory_zip, EXTRACT_DIR):
            total = process_images()
            write_manifest(factory_zip.name)
            print_summary()

            if total == 0:
                console.print("\n[bold red]FINISHED — no blobs were found.[/bold red]")
            else:
                console.print(f"\n[bold green]COMPLETE — {total} blob(s) extracted.[/bold green]")
    except Exception as e:
        console.print(f"[red]Fatal error: {e}[/red]")
        raise
    finally:
        if Confirm.ask("\nClean up temporary extraction files? (output is kept)"):
            shutil.rmtree(EXTRACT_DIR, ignore_errors=True)
            EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
        if Confirm.ask("Remove the factory zip from input/?"):
            factory_zip.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
