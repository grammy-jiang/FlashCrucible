"""Read MMC/SD card identity and wear data from the kernel and mmc-utils.

Every value here comes from the device. Nothing is synthesised: when a card or
reader cannot supply a field, the caller gets an error rather than a plausible
number. The previous implementation invented values from `hash(device_path)`,
which -- because Python randomises string hashing per process -- reported a
different serial number and wear figure for the same card on every run, and
those numbers were recorded in the run history as measurements.

Two sources, both real:

* CID/identity comes from sysfs (`/sys/block/<name>/device/...`). Cards on an
  MMC host controller expose the actual CID register fields there; USB card
  readers present a SCSI device instead and expose only reader identity, which
  is reported as such rather than dressed up as card identity.
* Wear data comes from `mmc extcsd read`, which is eMMC-only and generally
  needs root.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict

from tfqa.core.errors import RuntimeIOError, TimeoutError, ToolNotFoundError

SYSFS_BLOCK = Path("/sys/block")
MMC_CMD = "mmc"

# EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A/B report wear in 10% bands: 0x01 means
# 0-10% consumed, 0x0A means 90-100%, 0x0B means the estimate is exceeded.
LIFE_TIME_BAND_PERCENT = 10
LIFE_TIME_EXCEEDED = 0x0B

# EXT_CSD_PRE_EOL_INFO
PRE_EOL_LABELS = {0x01: "normal", 0x02: "warning", 0x03: "urgent"}

# Keys that constitute an actual wear reading. The raw life_time_est_typ_*
# values are reported as facts even when they read 0x00 ("not defined"), so the
# "did we learn anything?" check keys off these instead of dict length.
WEAR_KEYS = frozenset(
    {"life_used_percent", "life_time_exceeded", "pre_eol_info", "pre_eol_state"}
)

_EXTCSD_FIELD = re.compile(
    r"\[(?P<key>EXT_CSD_[A-Z0-9_]+)\]:\s*(?P<value>0x[0-9a-fA-F]+|\d+)"
)

# mmc-utils exits 0 even when the ioctl fails, so the output has to be checked.
_EXTCSD_FAILURE = re.compile(r"could not read ext_csd|ioctl:", re.IGNORECASE)


class MmcCid(TypedDict, total=False):
    manufacturer_id: int
    oem_id: int
    product_name: str
    product_revision: str
    serial_number: str
    manufacture_date: str
    cid_register: str
    # False when the values describe a USB reader rather than the card itself.
    is_card_identity: bool
    source: str


class MmcHealth(TypedDict, total=False):
    life_used_percent: int
    life_time_est_typ_a: int
    life_time_est_typ_b: int
    life_time_exceeded: bool
    pre_eol_info: int
    pre_eol_state: str
    source: str


__all__ = [
    "MmcCid",
    "MmcHealth",
    "read_cid",
    "read_health",
    "device_sysfs_dir",
    "parse_extcsd",
    "life_time_to_percent",
]


def device_sysfs_dir(device_path: str) -> Path:
    """Return the sysfs `device` directory backing a block device path."""

    name = Path(device_path).name
    directory = SYSFS_BLOCK / name / "device"
    if not directory.is_dir():
        raise RuntimeIOError(
            f"No sysfs entry for {device_path}",
            {"device_path": device_path, "expected": str(directory)},
        )
    return directory


def _read_attr(directory: Path, name: str) -> str | None:
    path = directory / name
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip() or None
    except (OSError, ValueError):
        # Several sysfs attributes raise ENXIO when the device cannot answer.
        return None


def _read_int_attr(directory: Path, name: str) -> int | None:
    raw = _read_attr(directory, name)
    if raw is None:
        return None
    try:
        return int(raw, 0)
    except ValueError:
        return None


def read_cid(device_path: str) -> MmcCid:
    """Return the card's identity as reported by the kernel.

    Raises RuntimeIOError when the kernel exposes no identity for the device.
    """

    directory = device_sysfs_dir(device_path)

    # A card attached to an MMC host controller exposes the real CID register.
    cid_register = _read_attr(directory, "cid")
    if cid_register:
        cid = MmcCid(
            product_name=_read_attr(directory, "name") or "",
            product_revision=_read_attr(directory, "fwrev") or "",
            serial_number=_read_attr(directory, "serial") or "",
            manufacture_date=_read_attr(directory, "date") or "",
            cid_register=cid_register,
            is_card_identity=True,
            source="sysfs:mmc",
        )
        manufacturer_id = _read_int_attr(directory, "manfid")
        if manufacturer_id is not None:
            cid["manufacturer_id"] = manufacturer_id
        oem_id = _read_int_attr(directory, "oemid")
        if oem_id is not None:
            cid["oem_id"] = oem_id
        return {key: value for key, value in cid.items() if value != ""}  # type: ignore[return-value]

    # USB card readers present a SCSI device. The identity belongs to the
    # reader, not the card, and is labelled accordingly.
    vendor = _read_attr(directory, "vendor")
    model = _read_attr(directory, "model")
    if vendor or model:
        reader = MmcCid(
            product_name=" ".join(part for part in (vendor, model) if part),
            product_revision=_read_attr(directory, "rev") or "",
            is_card_identity=False,
            source="sysfs:scsi",
        )
        return {key: value for key, value in reader.items() if value != ""}  # type: ignore[return-value]

    raise RuntimeIOError(
        f"No card identity available for {device_path}",
        {
            "device_path": device_path,
            "sysfs": str(directory),
            "hint": (
                "Cards behind a USB reader do not expose the CID register; "
                "attach the card to an MMC host controller to read it."
            ),
        },
    )


def life_time_to_percent(value: int) -> int | None:
    """Convert an EXT_CSD life-time estimate into an upper-bound percentage.

    The register reports 10% bands rather than a precise figure, so the band's
    upper bound is returned; 0x00 means "not defined" and yields None.
    """

    if value <= 0:
        return None
    if value >= LIFE_TIME_EXCEEDED:
        return 100
    return value * LIFE_TIME_BAND_PERCENT


def parse_extcsd(output: str) -> dict[str, int]:
    """Extract EXT_CSD register values from `mmc extcsd read` output."""

    return {
        match.group("key"): int(match.group("value"), 0)
        for match in _EXTCSD_FIELD.finditer(output)
    }


def read_health(device_path: str, *, timeout_seconds: float = 30.0) -> MmcHealth:
    """Return wear data from the card's EXT_CSD registers.

    Raises ToolNotFoundError when mmc-utils is absent, and RuntimeIOError when
    the device cannot answer -- which is the normal outcome for SD cards and
    for anything behind a USB reader, since EXT_CSD is an eMMC feature.
    """

    executable = shutil.which(MMC_CMD)
    if not executable:
        raise ToolNotFoundError(MMC_CMD)

    try:
        proc = subprocess.run(
            [executable, "extcsd", "read", device_path],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            "mmc extcsd read timed out",
            timeout_seconds,
            {"device_path": device_path, "tool": MMC_CMD},
        ) from exc
    except FileNotFoundError as exc:  # pragma: no cover - guard
        raise ToolNotFoundError(MMC_CMD) from exc

    combined = f"{proc.stdout}\n{proc.stderr}"
    fields = parse_extcsd(proc.stdout)

    # mmc-utils exits 0 on a failed ioctl, so an empty parse or an error line
    # is the only reliable signal.
    if proc.returncode != 0 or not fields or _EXTCSD_FAILURE.search(combined):
        raise RuntimeIOError(
            f"Could not read EXT_CSD from {device_path}",
            {
                "device_path": device_path,
                "exit_code": proc.returncode,
                "stderr": proc.stderr.strip()[:500],
                "hint": (
                    "EXT_CSD is an eMMC feature and usually needs root; SD "
                    "cards and USB readers do not provide it."
                ),
            },
        )

    typ_a = fields.get("EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A")
    typ_b = fields.get("EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_B")
    pre_eol = fields.get("EXT_CSD_PRE_EOL_INFO")

    health = MmcHealth(source="mmc-utils:extcsd")
    if typ_a is not None:
        health["life_time_est_typ_a"] = typ_a
    if typ_b is not None:
        health["life_time_est_typ_b"] = typ_b

    # 0x00 means "not defined" for all three registers, so a card that answers
    # with zeros has supplied no wear estimate. Treating it as data would let
    # the snapshot claim health is available when nothing was measured.
    estimates = [value for value in (typ_a, typ_b) if value]
    if estimates:
        worst = max(estimates)
        health["life_time_exceeded"] = worst >= LIFE_TIME_EXCEEDED
        percent = life_time_to_percent(worst)
        if percent is not None:
            health["life_used_percent"] = percent

    if pre_eol:
        health["pre_eol_info"] = pre_eol
        health["pre_eol_state"] = PRE_EOL_LABELS.get(pre_eol, "unknown")

    if not health.keys() & WEAR_KEYS:
        raise RuntimeIOError(
            f"EXT_CSD from {device_path} reported no wear estimate",
            {
                "device_path": device_path,
                "fields_found": sorted(fields)[:20],
                "hint": (
                    "The lifetime and pre-EOL registers read as 0x00, which the "
                    "eMMC spec defines as 'not defined'."
                ),
            },
        )

    return health
