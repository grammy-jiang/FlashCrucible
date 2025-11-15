# FlashCrucible

## Popular microSD/TF Card Testing Tools

| Tool | Platforms | Primary Focus | Key Features | Notable Limitations |
| --- | --- | --- | --- | --- |
| **F3 (Fight Flash Fraud)** | Linux, macOS, Windows (via WSL/Cygwin) | Capacity verification & counterfeit detection | Sequential write/read (f3write/f3read), destructive probe (f3probe), quick non-destructive scan (f3qt); plain-text logs | Destructive by default, slow on large cards, limited performance metrics |
| **H2testw** | Windows | Capacity verification | Simple GUI, single-button test, verifies full storage by writing/reading pattern files | Very slow full-card sweeps, Windows-only, no automation interface |
| **badblocks** | Linux | Surface & error scanning | Configurable read/write patterns, destructive and non-destructive modes, integrates with e2fsck | Command-line heavy, limited reporting, no smart counterfeit detection |
| **fio (Flexible I/O Tester)** | Linux, macOS, Windows | Performance benchmarking & stress | Highly configurable workloads, random/sequential IO, latency statistics, scripting-friendly JSON output | Steep learning curve, not tailored to flash counterfeits, requires manual setup |
| **CrystalDiskMark** | Windows | Synthetic performance benchmarking | GUI-driven sequential/random throughput tests, supports NVMe/USB/SD, widely recognized results | No capacity verification, Windows-only, limited automation |
| **A1 SD Bench** | Android | On-device performance benchmarking | Tests installed cards without host PC, sequential/random tests, history tracking | Mobile-only, limited diagnostic depth, ad-supported |
| **Blackmagic Disk Speed Test** | macOS | Media-focused performance benchmarking | Real-time video capture workload simulation, simple interface | macOS-only, no integrity or counterfeit detection |
| **Disk Utility + `diskutil` verifyDisk** | macOS | Filesystem integrity & surface scan | Native tooling, integrates with SMART via USB readers, repairs FAT/exFAT | Limited performance insight, limited counterfeit detection |
| **USBDeview + HWiNFO combo** | Windows | Health & interface diagnostics | Reports controller IDs, power characteristics, SMART passthrough (if available) | Requires multiple tools, manual correlation, no automated sweeps |

These tools collectively cover the typical needs of microSD/TF card validation: verifying actual capacity, surface integrity scanning, performance benchmarking, and accessing health/SMART data where supported. FlashCrucible can wrap or replicate these capabilities while providing cohesive automation, cross-platform reporting, and safety guardrails.

## Testing

⚠️ Tests not run (informational documentation update).
