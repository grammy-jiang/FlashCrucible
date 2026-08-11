# Study of Existing microSD/TF Card Test Tools  
现有 microSD/TF 卡测试工具研究

> 建议文件名：`docs/sd-tools-study-v0.md`  
> 本文梳理现有常见 microSD/TF 测试工具，按**测试功能点**归类，并在最后给出一个**特性 + 平台/系统对比表**，为后续 Python CLI 工具设计提供参考。

---

## 1. Major Test Function Buckets / 主要测试功能桶

### 1.1 Capacity & Authenticity / 容量与真伪

**English**

- Check whether the **reported capacity** matches the **real physical capacity**.
- Detect **fake / over-reported** cards (e.g. 256 GB label but only 32 GB real NAND).
- Often implemented as **write known pattern → read back → verify** across the (whole or partial) device.

**中文**

- 检查卡片**固件上报容量**是否等于**真实物理容量**；
- 识别 **虚标 / 超报容量** 的假卡（例如标 256 GB、实际只有 32 GB 闪存）；
- 通常通过“**写入已知模式 → 读回 → 校验**”实现，可以覆盖全盘或部分区域。

**Representative tools / 代表工具**

- **H2testw (Windows)** – writes/reads test patterns to verify actual size; originally created to combat counterfeit SD/USB devices.  
- **F3 – Fight Flash Fraud (Linux; macOS ports)** – open-source H2testw-style tool; `f3write`/`f3read` for full test; `f3probe` for fast real-size detection; `f3fix` to shrink to true size.  
- Various **H2testw clones / alternatives** (ValiDrive, Quick Disk Test, etc.) that replicate the same principle.

---

### 1.2 Surface Integrity & Bad-Block Scanning / 表面完整性与坏块扫描

**English**

- Locate **bad sectors** and unstable regions:
  - hard read/write errors,
  - sectors failing intermittently,
  - clusters of problematic blocks.
- Operate directly at **raw block** level, below filesystem.

**中文**

- 查找**坏块**和**不稳定区域**：
  - 硬错误（读写直接失败）；
  - 间歇性失败扇区；
  - 成片问题扇区聚集；
- 在**块设备层**工作，而不是文件系统层。

**Representative tools / 代表工具**

- **badblocks (Linux)** – classic utility to scan block devices with read-only or destructive read-write patterns, logging bad sectors; widely used for HDD/SSD but applies to SD too.  
- Some workflows reuse **F3/H2testw full tests** as de facto surface scans (full-device write+verify exposes bad regions).

---

### 1.3 Performance Benchmarking / 性能基准测试

**English**

- Measure **sequential** read/write throughput (MB/s).
- Measure **random** I/O performance (IOPS, latency) for small blocks.
- Sometimes emulate specific workloads (e.g. video recording, camera bursts).

**中文**

- 测量**顺序**读写吞吐（MB/s）；  
- 测量小块**随机 I/O** 性能（IOPS、延迟）；  
- 有时模拟特定场景（如视频写入、连拍缓存）。

**Representative tools / 代表工具**

- **CrystalDiskMark (Windows)** – very common GUI benchmark; measures sequential and random read/write at different sizes and queue depths.  
- **Generic disk benchmarks** – ATTO Disk Benchmark, AJA System Test, Blackmagic Disk Speed Test etc. commonly used for memory cards.  
- **fio (Linux, cross-platform)** – flexible CLI workload generator; with appropriate jobs it can benchmark SD cards for seq/rand R/W and mixed workloads.  
- **Android apps** – A1 SD Bench, AndroBench, “SD Card Speed Test”, etc., providing sequential and random benchmarks on Android devices.  

---

### 1.4 Endurance & Burn-in / 耐久与烧机

**English**

- Long-term stress tests focusing on:
  - total bytes written (TBW),
  - error rate vs time,
  - performance degradation (throttling / GC effects),
  - early failures under sustained use.

**中文**

- 长时间、高写入量的压力测试，关注：
  - 累计写入量（TBW）；
  - 错误率随时间的变化；
  - 性能退化（降速 / 垃圾回收影响）；
  - 高频使用下的早期失效。

**Reality / 现状**

- There is **no mainstream single-purpose “SD endurance CLI”**:
  - People loop H2testw/F3 runs, or
  - Use `fio` with long-running jobs, plus
  - Home-grown scripts to log errors, throughput and sometimes temperature.

This is clearly an **underserved area**, which a new tool can own.

---

### 1.5 Health & Metadata / 健康与元数据

**English**

- Read **CID, CSD, EXT_CSD** registers:
  - manufacturer ID, product name,
  - serial number, manufacturing date,
  - capacity descriptor and feature flags.
- On industrial cards, query **health / SMART-like status** via vendor-specific mechanisms (often CMD56-based).

**中文**

- 读取 **CID、CSD、EXT_CSD** 等寄存器：
  - 厂商 ID、产品名；
  - 序列号、生产日期；
  - 容量、特性标志；
- 在工业卡上，通过厂商私有协议（多基于 CMD56）获取类似 **SMART 的健康状态**。

**Representative tools / 代表工具**

- **mmc-utils (`mmc`) (Linux)** – official MMC utility to read and parse configuration registers (incl. EXT_CSD) and perform MMC/SD maintenance operations.  
- **sdmon (Linux)** – reads health data of certain industrial/high-endurance SD cards (Apacer, Kingston Industrial/High Endurance, SanDisk Industrial, WD Purple, etc.) using CMD56-based vendor commands.  

**Observation / 观察**

- There is **no universal SMART** for SD; health reporting is vendor-specific. `sdmon` shows what is possible for supported cards; elsewhere you fall back to register decoding + behavioral metrics.

---

### 1.6 Filesystem-Level Checks / 文件系统级检查

**English**

- Validate **filesystem integrity**:
  - metadata consistency (superblocks, allocation tables),
  - logical errors caused by flaky media.
- Simulate **application-level workloads**:
  - many small file creates/deletes,
  - directory traversal,
  - logging patterns, etc.

**中文**

- 验证**文件系统一致性**：
  - 元数据是否损坏（超级块、分配表等）；
  - 是否存在由底层闪存问题导致的逻辑错误；
- 模拟**应用层工作负载**：
  - 大量小文件创建/删除；
  - 目录树操作；
  - 日志写入模式等。

**Representative tools / 代表工具**

- `fsck` / `e2fsck` / `fsck.fat` / `chkdsk` – generic FS consistency checkers.
- Custom stress scripts – big trees of tiny files, timed operations.

---

### 1.7 Image Flashing & Verification / 镜像写入与校验

**English**

- Provisioning OS images to SD cards and optionally verifying:
  - Raspberry Pi, SBC images,
  - router firmware, etc.
- Typical flow:
  - write image,
  - read back or hash-verify critical parts.

**中文**

- 将操作系统镜像写入 SD 卡，并可选对写入结果进行校验：  
  - 树莓派 / SBC 系统镜像；
  - 路由固件等；
- 一般流程：
  - 写镜像；
  - 读回 / 计算哈希进行关键区域的比对。

**Representative tools / 代表工具**

- GUI flashers: **Raspberry Pi Imager**, **balenaEtcher**, **Win32DiskImager** (often offer optional verify pass).
- Pure CLI: `dd` + `cmp` / `sha256sum` verification.

---

### 1.8 Reporting & Automation / 报告与自动化

**English**

- Human-oriented logs / progress, plus
- Machine-oriented outputs (CSV, JSON, exporters) for:
  - CI pipelines,
  - lab dashboards.

**中文**

- 面向人类的日志 / 进度输出；  
- 面向机器的结构化结果（CSV、JSON、指标等），用于：
  - CI 流水线；
  - 实验室 QA 仪表盘。

**Reality / 现状**

- Most existing tools (H2testw, F3, CrystalDiskMark, etc.) only emit **plain text or GUI output**, not structured JSON; serious users wrap them in scripts and parse text.

---

## 2. Representative Tools – Short Profiles / 典型工具简要画像

下面简要列出后面表格中要用到的代表性工具：

1. **H2testw** – Windows GUI, detects fake capacity and basic errors via full-device write+verify.  
2. **F3 (Fight Flash Fraud)** – Linux CLI (with macOS ports), open source; capacity & performance tests (`f3write`/`f3read`/`f3probe`/`f3fix`…).  
3. **mmc-utils (`mmc`)** – Linux CLI; reads/parses SD/MMC registers and config; basis for low-level health/feature interrogation.  
4. **sdmon** – Linux CLI; vendor-specific health monitor for several industrial/high-endurance SD card families.  
5. **badblocks** – Linux CLI; generic surface scan & bad-block logger.  
6. **fio** – Cross-platform CLI; general I/O workload generator used heavily for performance and endurance testing.  
7. **CrystalDiskMark** – Windows GUI; mainstream sequential/random benchmark.  
8. **ATTO/AJA/Blackmagic tests** – cross-platform GUI benchmarks useful for card speed validation (especially in photo/video workflows).  
9. **Android SD speed apps** – A1 SD Bench, AndroBench, SD Card Speed Test etc., used to benchmark SD performance directly on phones/tablets.  

---

## 3. Feature Comparison Table / 功能与平台对比表

> 说明：  
> - **✓** = 明确支持 / 主要用途  
> - **◑** = 间接支持 / 可通过配置实现  
> - **✗** = 不支持或不是设计目标  
> - “Platform/OS” 简单合并“平台形态 + 操作系统”

### 3.1 High-level Feature vs Tool / 高层功能对比

| Tool               | Interface | Primary Focus                                      | Capacity / Fake Detection | Surface Scan / Bad Blocks | Perf Benchmark (Seq/Rand) | Endurance / Burn-in | Health / Metadata (CID/CSD/EXT_CSD, SMART-like) | Image Flash & Verify | Automation-friendly (CLI / scriptable) | Platform / OS                    |
|--------------------|-----------|----------------------------------------------------|---------------------------|---------------------------|---------------------------|---------------------|--------------------------------------------------|----------------------|----------------------------------------|----------------------------------|
| **H2testw**        | GUI       | Capacity integrity, counterfeit detection          | ✓ Full-device pattern test | ◑ (errors imply bad areas) | ◑ Basic throughput shown | ✗                   | ✗                                                | ✗ (not general flasher) | Low (GUI only, no JSON)               | Windows                          |
| **F3**             | CLI       | Capacity + authenticity + basic perf               | ✓ (`f3write/f3read`, `f3probe`) | ◑ (full test ≈ surface)     | ✓ (seq write/read metrics) | ◑ (looped runs via script) | ✗ (no register decoding itself)                 | ✗                    | High (CLI, easy to script)            | Linux, macOS ports               |
| **mmc-utils**      | CLI       | Low-level MMC/SD config and registers              | ✗                           | ✗                         | ✗                         | ✗                   | ✓ (`cid`, `csd`, `extcsd read`, etc.)            | ✗                    | High (CLI, scriptable)                | Linux                            |
| **sdmon**          | CLI       | Industrial SD health monitoring (vendor-specific)  | ✗                           | ✗                         | ✗                         | ◑ (trend health vs time) | ✓ (health %, power-on count, etc. for supported cards) | ✗                    | High (CLI, parse-friendly)            | Linux                            |
| **badblocks**      | CLI       | Raw surface scan / bad-block detection             | ◑ (can reveal fake behavior) | ✓ (read/read-write patterns) | ✗                         | ◑ (long multi-pass runs) | ✗                                                | ✗                    | High (CLI, widely scripted)           | Linux / other *nix               |
| **fio**            | CLI       | Flexible I/O workloads & performance               | ✗ (not fake-capacity oriented) | ◑ (can expose weak sectors) | ✓ (seq + rand, configurable) | ✓ (long-run jobs)   | ✗ (media-agnostic, no register access)          | ✗                    | High (rich CLI & config files)        | Linux, Windows, BSD, macOS       |
| **CrystalDiskMark**| GUI       | Seq/rand performance metrics                        | ✗                           | ✗                         | ✓ (seq/rand 4K etc.)      | ✗                   | ✗                                                | ✗                    | Low (GUI, no structured output)       | Windows                          |
| **ATTO/AJA/Blackmagic** | GUI  | Performance for camera/video workflows             | ✗                           | ✗                         | ✓ (seq perf, sometimes patterns) | ✗               | ✗                                                | ✗                    | Low                                   | Windows, macOS (varies by tool)  |
| **Android SD Bench / AndroBench / etc.** | GUI (app) | On-device SD speed testing                         | ✗                           | ✗                         | ✓ (seq/random on Android) | ✗                   | ✗                                                | ✗                    | Medium (some export, mostly manual)   | Android                          |

---

### 3.2 Platform & OS Summary / 平台与系统支持概览

| Tool / Family                    | Form Factor         | Main OS / Platform                                       |
|----------------------------------|---------------------|----------------------------------------------------------|
| H2testw                          | Native GUI          | Windows only                                             |
| F3 (f3write/f3read/f3probe…)     | Native CLI          | Linux; F3X/F3XSwift ports on macOS                      |
| mmc-utils (`mmc`)                | Native CLI          | Linux (requires MMC/SD exposed via Linux kernel)        |
| sdmon                            | Native CLI          | Linux (requires raw SD access & specific industrial cards) |
| badblocks                        | Native CLI          | Linux / *nix                                            |
| fio                              | Native CLI          | Linux, BSD, macOS, Windows                              |
| CrystalDiskMark                  | Native GUI          | Windows                                                  |
| ATTO Disk Benchmark              | Native GUI          | Windows                                                  |
| AJA System Test                  | Native GUI          | Windows, macOS                                          |
| Blackmagic Disk Speed Test       | Native GUI          | macOS, Windows                                          |
| A1 SD Bench, AndroBench, etc.    | Mobile app (GUI)    | Android (phones/tablets; some features vary by device)  |

---

## 4. Takeaways for Future Tool Design / 对后续工具设计的启示（简要）

**English**

- **Capacity/fake detection** is well-served (H2testw, F3), but they lack:
  - structured logs and JSON,
  - AI/automation-friendly interfaces,
  - integrated health & endurance views.
- **Health & endurance** are **poorly covered** and fragmented (`mmc-utils`, `sdmon`, custom `fio` scripts).
- Very few tools span **multiple buckets in a coherent way**.

A new Python CLI can:

1. Wrap existing best-in-class tools (F3, mmc-utils, sdmon) where they are strong.  
2. Implement missing parts (endurance, workload profiling, structured reporting).  
3. Present a unified, automation-ready interface (CLI + JSON) over all feature buckets.

**中文**

- **容量 / 真伪检测**已经有 H2testw、F3 等成熟方案，但它们缺少：
  - 结构化日志与 JSON 输出；
  - 面向 AI / 自动化的接口；
  - 与健康、耐久测试的一体化视图。
- **健康与耐久**目前明显是**碎片化、欠缺的**（`mmc-utils`、`sdmon` + 自写 `fio` 脚本）。  
- 几乎没有工具能在**多个功能桶之间提供统一、一致的 QA 体验**。

新的 Python CLI 工具可以：

1. 在成熟能力处做高质量封装（F3、mmc-utils、sdmon）；  
2. 在缺失部分（耐久、复杂工作负载、报告与可视化）自行补齐；  
3. 通过统一的 CLI 和 JSON 协议，把这些功能桶串联成真正意义上的 microSD/TF QA 平台。

---

**End of document / 文档结束**
