# FlashCrucible

## Popular microSD/TF Card Testing Tools (Linux-Focused)

FlashCrucible will run on Linux (x86_64, arm32, arm64). The table below highlights tools that can be wrapped directly on Linux along with any functionality gaps that must be implemented natively when no Linux-friendly alternative exists.

| Tool | Linux Support | Primary Focus | Wrapper Suitability | Notes & Gaps |
| --- | --- | --- | --- | --- |
| **F3 (Fight Flash Fraud)** | Native packages for most distros, source builds on ARM | Capacity verification & counterfeit detection | ✅ Full wrapper possible (`f3write`, `f3read`, `f3probe`, `f3qt`) | Provides comprehensive fake-capacity detection; Python layer should manage destructive mode warnings |
| **badblocks** | Core util-linux component on Linux | Surface scan & bad block detection | ✅ Wrapper via util-linux | Works for destructive or non-destructive sweeps; combine with filesystem repair utilities (`e2fsck`) for integrated workflows |
| **fio (Flexible I/O Tester)** | Widely packaged on Linux, runs on ARM | Performance benchmarking & stress | ✅ Wrapper using JSON output | Supplies granular throughput, latency, and endurance profiles; FlashCrucible can ship curated job files for common workloads |
| **dd / pv** | Built-in coreutils | Simple sequential read/write tests | ✅ Direct invocation | Use for quick sanity checks, image creation, or fall back when `fio` unavailable; pair with hashing for verification |
| **mmc-utils** | Available on Linux, including ARM | Card register inspection & tuning | ✅ Wrapper for `mmc extcsd`, `mmc status`, etc. | Enables CID/CSD register dumps, cache control, and secure erase when hardware supports MMC commands |
| **smartctl (smartmontools)** | Supports Linux USB-SD bridges with pass-through | Health monitoring | ⚠️ Conditional wrapper (depends on reader) | Only certain USB readers expose SMART; Python tool should detect capability and gracefully degrade |
| **lsblk / blkid / udevadm** | Core Linux utilities | Device discovery & metadata | ✅ Wrapper | Gather size, bus info, filesystem type, serials; essential for safe device selection |
| **fsck.* (e2fsck, fsck.vfat, fsck.exfat)** | Native Linux filesystem tools | Filesystem integrity checks | ✅ Wrapper with safety prompts | Use after unmounting partitions; coordinate with mount helpers to avoid data loss |
| **rsync / hashdeep / sha256sum** | Widely available | Data comparison & checksums | ✅ Wrapper or native hashlib | Support golden-image verification and bit-rot detection; fallback to Python hashing when binaries missing |
| **blkdiscard / hdparm / sdparm** | Native to Linux | Secure erase & trim | ✅ Wrapper (when kernel/device support) | Provide secure wipe and TRIM verification; require capability probing to avoid unsupported commands |
| **stress-ng** | Linux utility | Long-running stress & thermal profiling | ✅ Optional wrapper | Can complement endurance testing by loading system or performing I/O soak |

### Non-Linux Tools & Required Native Implementations

Some well-known utilities are Windows- or macOS-only. FlashCrucible cannot wrap them directly on Linux, so comparable capabilities must come from Linux-native tools or custom Python implementations.

| Tool | Platform | Capability Gap | Linux Replacement Strategy |
| --- | --- | --- | --- |
| **H2testw** | Windows | Whole-card write/read counterfeit detection | Use `f3write/f3read` or integrate native Python pattern-writer/reader for environments where F3 unavailable |
| **CrystalDiskMark** | Windows | GUI performance benchmarking presets | Provide curated `fio` job profiles and human-readable summaries to emulate popular benchmark metrics |
| **Blackmagic Disk Speed Test** | macOS | Video-centric performance simulation | Bundle `fio` media workload profiles (e.g., large sequential writes/reads at 4K/8K bitrates) |
| **A1 SD Bench** | Android | On-device quick tests | Offer lightweight `fio` or Python-based benchmarks with presets matching A1/A2 specs |
| **USBDeview + HWiNFO** | Windows | USB controller insights & SMART passthrough | Combine `lsusb`, `udevadm`, and `smartctl` (when available) plus custom parsing of sysfs to expose controller IDs and power data |

By centering on Linux-compatible tooling, FlashCrucible can wrap mature utilities where possible and fill remaining gaps with native Python implementations or alternative Linux command-line tools.

## Testing

⚠️ Tests not run (informational documentation update).
