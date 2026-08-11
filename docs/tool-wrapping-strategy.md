# Tool Wrapping Strategy

Which existing utilities FlashCrucible wraps, which it cannot, and what has to be built natively.

FlashCrucible runs on Linux (x86_64, arm32, arm64) with a focus on the Debian/Ubuntu and
Fedora/CentOS/RHEL families. The tables below record the wrapping decision for each tool along with
the functionality gaps that must be implemented in Python when no Linux-friendly alternative
exists.

For a broader survey of the tool landscape (including non-wrapped tools and their feature
comparison), see [sd-tools-study-v0.md](sd-tools-study-v0.md).

## Linux tools we wrap

| Tool | Linux Support | Primary Focus | Wrapper Suitability | Notes & Gaps |
| ---- | ------------- | ------------- | ------------------- | ------------ |
| **F3 (Fight Flash Fraud)** | Native packages for most distros, source builds on ARM | Capacity verification & counterfeit detection | ✅ Full wrapper possible (`f3write`, `f3read`, `f3probe`, `f3qt`) | Provides comprehensive fake-capacity detection; Python layer should manage destructive mode warnings |
| **badblocks** | Core util-linux component on Linux | Surface scan & bad block detection | ✅ Wrapper via util-linux | Works for destructive or non-destructive sweeps; combine with filesystem repair utilities (`e2fsck`) for integrated workflows |
| **fio (Flexible I/O Tester)** | Widely packaged on Linux, runs on ARM | Performance benchmarking & stress | ✅ Wrapper using JSON output | Supplies granular throughput, latency, and endurance profiles; FlashCrucible can ship curated job files for common workloads |
| **dd / pv** | Built-in coreutils | Simple sequential read/write tests | ✅ Direct invocation | Use for quick sanity checks, image creation, or fall back when `fio` unavailable; pair with hashing for verification |
| **mmc-utils** | Available on Linux, including ARM | Card register inspection & tuning | ✅ Wrapper for `mmc extcsd`, `mmc status`, etc. | Enables CID/CSD register dumps, cache control, and secure erase when hardware supports MMC commands |
| **sdmon** | Static Linux builds (armv7, arm64, x86_64) | Industrial/high-endurance SD card health readout via CMD56 | ⚠️ Conditional wrapper | Outputs JSON health metrics for supported industrial cards; requires direct MMC access (e.g. `/dev/mmcblk0`) and only works on cards that implement vendor CMD56 |
| **smartctl (smartmontools)** | Supports Linux USB-SD bridges with pass-through | Health monitoring | ⚠️ Conditional wrapper (depends on reader) | Only certain USB readers expose SMART; the tool should detect capability and gracefully degrade |
| **lsblk / blkid / udevadm** | Core Linux utilities | Device discovery & metadata | ✅ Wrapper | Gather size, bus info, filesystem type, serials; essential for safe device selection |
| **fsck.\* (e2fsck, fsck.vfat, fsck.exfat)** | Native Linux filesystem tools | Filesystem integrity checks | ✅ Wrapper with safety prompts | Use after unmounting partitions; coordinate with mount helpers to avoid data loss |
| **rsync / hashdeep / sha256sum** | Widely available | Data comparison & checksums | ✅ Wrapper or native hashlib | Support golden-image verification and bit-rot detection; fall back to Python hashing when binaries are missing |
| **blkdiscard / hdparm / sdparm** | Native to Linux | Secure erase & trim | ✅ Wrapper (when kernel/device support) | Provide secure wipe and TRIM verification; require capability probing to avoid unsupported commands |
| **stress-ng** | Linux utility | Long-running stress & thermal profiling | ✅ Optional wrapper | Can complement endurance testing by loading system or performing I/O soak |

## Non-Linux tools and their replacements

Some well-known utilities are Windows- or macOS-only. FlashCrucible cannot wrap them on Linux, so
comparable capabilities must come from Linux-native tools or custom Python implementations.

| Tool | Platform | Capability Gap | Linux Replacement Strategy |
| ---- | -------- | -------------- | -------------------------- |
| **H2testw** | Windows | Whole-card write/read counterfeit detection | Use `f3write`/`f3read`, or a native Python pattern-writer/reader where F3 is unavailable |
| **CrystalDiskMark** | Windows | GUI performance benchmarking presets | Provide curated `fio` job profiles and human-readable summaries that emulate the popular benchmark metrics |
| **Blackmagic Disk Speed Test** | macOS | Video-centric performance simulation | Bundle `fio` media workload profiles (e.g. large sequential writes/reads at 4K/8K bitrates) |
| **A1 SD Bench** | Android | On-device quick tests | Offer lightweight `fio` or Python-based benchmarks with presets matching A1/A2 specs |
| **USBDeview + HWiNFO** | Windows | USB controller insights & SMART passthrough | Combine `lsusb`, `udevadm`, and `smartctl` (when available) plus custom sysfs parsing to expose controller IDs and power data |

By centring on Linux-compatible tooling, FlashCrucible wraps mature utilities where possible and
fills the remaining gaps with native Python implementations or alternative Linux command-line
tools.

## Python dependency stack

### Development tooling

FlashCrucible targets Python 3.13+ and uses `uv` for fast installs, virtualenv creation, and
reproducible `pyproject.toml` workflows.

| Category | Tool | Purpose | Notes |
| -------- | ---- | ------- | ----- |
| Env & packaging | **uv** | Dependency resolution, builds, and venv management | `uv sync` to bootstrap, `uv run` for entrypoints |
| Linting/format | **ruff** | Linting and code formatting | Single tool for style enforcement |
| Type checking | **mypy** | Static typing | Aim for strictness on CLI and subprocess layers |
| Testing | **pytest**, **pytest-asyncio** | Unit/integration tests | Covers CLI smoke tests and destructive command simulation |

### Runtime dependencies

Packages currently required, plus the ones the design anticipates. Only the first group is in
`pyproject.toml` today.

| Category | Dependency | Purpose | Status |
| -------- | ---------- | ------- | ------ |
| CLI framework | **Typer** (Click-compatible) | Declarative CLI with subcommands and automatic help | In use |
| Terminal UX | **Rich** | Coloured output, progress bars, tables | In use |
| Configuration | **Pydantic** v2 | Structured settings, typed models, serialization | In use |
| OS integration | **platformdirs** | Per-user cache/config/state paths | In use |
| Validation | **jsonschema** | Validate the shipped JSON schemas and CLI payloads | In use |
| Concurrency | **AnyIO** | Unified async/sync subprocess management for long-running tools like `fio` | Planned |
| Process control | **python-dotenv**, **subprocess-tee** | Environment loading, teeing stdout/stderr | Planned |
| Serialization | **PyYAML**, **orjson** | YAML for user-authored plans, fast JSON for report dumps | Planned |
| Logging | **structlog** | Structured logging routed to files and console | Planned |
| OS integration | **psutil** | Device metadata and resource monitoring | Planned |
| Optional hardware | **pyudev**, **pyusb** | Advanced device discovery, USB bridge inspection | Planned — gate on availability to keep installs light |
| Reporting | **Jinja2** | HTML/Markdown report templates | Planned |

Beyond Python packages, FlashCrucible depends on the Linux-native utilities above. The CLI probes
for them at runtime (`tfqa capabilities`), should provide installation hints (`apt`, `dnf`,
`pacman`), and falls back to native Python implementations where binaries are unavailable.
