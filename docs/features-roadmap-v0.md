# tfqa – Feature Roadmap & Priorities

极其硬核 microSD/TF QA 工具功能规划与优先级

> 建议文件名：`docs/features-roadmap-v0.md`  
> 本文基于现有 microSD/TF 测试工具调研与“生产级、极其硬核”目标，对功能进行分解，并给出重要性、实现优先级与建议实现顺序，同时标注是“封装三方工具（Wrapper）”还是“自研实现（Native）”。

---

## 1. Feature Table / 功能优先级总表

### Legend / 标注说明

- **Importance**: High / Medium / Low
- **Priority**: P0 (immediate), P1 (next), P2 (later), P3 (nice-to-have)
- **Sequence**: Phase 0/1/2/3…（实现阶段顺序）
- **Strategy**: Wrapper(封装), Native(自研), Hybrid(封装 + 自研补强)

---

### 1.1 Core & Device Safety / 核心与设备安全

| ID  | Feature                                         | Description (EN / 中文)                                                                                                                                                        | Importance | Priority | Sequence | Strategy | Notes                                                      |
| --- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------- | -------- | -------- | -------- | ---------------------------------------------------------- |
| C1  | Device discovery & classification               | Enumerate block devices, detect removable, size, model, vendor; flag likely system disks, mounts. 设备枚举、识别 U 盘/TF/系统盘、挂载点等                                      | High       | P0       | Phase 0  | Native   | 基础设施；所有测试前提；要做得非常可靠和保守（安全优先）。 |
| C2  | Safety guardrails for destructive tests         | Prevent destructive ops on system disk / mounted devices unless explicitly forced; double-confirm in interactive mode; safe defaults in AI/CI mode. 安全护栏（防止误刷系统盘） | High       | P0       | Phase 0  | Native   | 这是“不会搞死人”的底线；必须从第一行代码就考虑。           |
| C3  | Configuration system (CLI + env + config files) | Layered config: defaults → system → user → project → env → CLI. 配置系统（多层覆盖）                                                                                           | High       | P0       | Phase 0  | Native   | 和 UX、AI 接口紧密相关；早期确定格式（比如 TOML）。        |
| C4  | Logging & JSONL event stream                    | Human-readable logs + structured JSONL per run (run_id). 日志与 JSONL 事件流                                                                                                   | High       | P0       | Phase 0  | Native   | 后面所有报告/可视化/分析都靠它；v0 必须有基本形态。        |
| C5  | Capabilities detection                          | Detect presence/version of f3, mmc-utils, sdmon, fio, etc., expose via `tfqa capabilities`. 能力/外部工具探测                                                                  | High       | P0       | Phase 0  | Native   | 决定 Wrapper vs fallback 的基础。                          |

---

### 1.2 Capacity & Authenticity / 容量与真伪

| ID  | Feature                                             | Description                                                                                                                                      | Importance | Priority | Sequence | Strategy                            | Notes                                                  |
| --- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ---------- | -------- | -------- | ----------------------------------- | ------------------------------------------------------ |
| A1  | Quick capacity/authenticity test                    | Fast test using `f3probe` or partial write+verify; non-destructive by default (use free space). 快速容量/真伪检测                                | High       | P0       | Phase 1  | Wrapper + Native fallback           | 这是主卖点之一；先做 wrapper，缺 F3 时用自研抽样算法。 |
| A2  | Full-device capacity & integrity test (destructive) | `f3write` + `f3read`-like full write+verify of entire device; destructive; provides real capacity + basic reliability. 全盘破坏性容量/完整性测试 | High       | P0       | Phase 1  | Wrapper + Native fallback           | 典型“上架前烧一次”的功能；要求 UX 极度安全。           |
| A3  | Auto-fix partition/filesystem to real capacity      | `f3fix`-style shrink to actual safe size after detecting fake capacity. 假卡“缩容修复”                                                           | Medium     | P1       | Phase 2  | Wrapper first (f3fix), later Native | 可以后做；在部分生产流程有用，但不是最早必须。         |

---

### 1.3 Surface Integrity / 表面完整性 & 坏块

| ID  | Feature                               | Description                                                                                        | Importance | Priority | Sequence | Strategy                                | Notes                                                                                              |
| --- | ------------------------------------- | -------------------------------------------------------------------------------------------------- | ---------- | -------- | -------- | --------------------------------------- | -------------------------------------------------------------------------------------------------- |
| S1  | Read-only surface scan                | Non-destructive scan using read patterns (badblocks -n style but read-only). 只读表面扫坏块        | Medium     | P1       | Phase 2  | Wrapper (badblocks) + optional Native   | 面向已经上线的卡做“健康巡检”；可以稍微后置。统计每次 pass 的覆盖/延迟，并附带 MMC+SDMON 健康快照。 |
| S2  | Destructive surface scan (multi-pass) | Write-read patterns across device (like badblocks -w) to find unstable regions. 破坏性多轮坏块扫描 | High       | P1       | Phase 2  | Wrapper (badblocks/F3) + Native options | 对“要做严肃 QA 的批次”非常重要；和 A2 有部分重叠。                                                 |

---

### 1.4 Performance Benchmarking / 性能基准

| ID  | Feature                                 | Description                                                                                                                        | Importance | Priority | Sequence  | Strategy                               | Notes                                                                                                           |
| --- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ---------- | -------- | --------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| P1  | Basic sequential throughput test        | Measure seq read/write MB/s at 1M/4M etc., single queue depth; SD spec-style. 基础顺序读写测速                                     | High       | P1       | Phase 2   | Native (simple) / optional fio wrapper | Easy to implement；对用户“感知价值”很高。优先通过 `fio` 获取真实 MB/s、延迟与 IOPS，缺失时退回模拟。            |
| P2  | Random I/O benchmark                    | 4K/16K random read/write/ mixed IOPS & latency; useful for SBC/router workloads. 小块随机 I/O 测试                                 | High       | P1       | Phase 2–3 | Wrapper(fio) + Native-lite             | 这是你和传统“测速软件”的差异点之一。利用 `fio randrw` + rwmixread，并附带健康快照，帮助理解现实负载下的卡健康。 |
| P3  | Profile presets (camera / SBC / router) | Predefined workloads: camera (large sequential writes), SBC rootfs (small random writes), logging heavy, etc. 预设工作负载 Profile | Medium     | P2       | Phase 3   | Native                                 | 设计成 config + profile（例如 `camera-logger.toml`、`router-telemetry.toml`），方便扩展。                       |

---

### 1.5 Endurance & Burn-in / 耐久与烧机

| ID  | Feature                         | Description                                                                                                  | Importance | Priority | Sequence  | Strategy                  | Notes                                        |
| --- | ------------------------------- | ------------------------------------------------------------------------------------------------------------ | ---------- | -------- | --------- | ------------------------- | -------------------------------------------- |
| E1  | Simple burn-in test             | Long-running loop: write+verify cycles over card; log errors and throughput over time. 简单耐久/烧机测试     | High       | P1       | Phase 3   | Hybrid (fio / own engine) | 现有工具明显缺位；这是你“硬核”名片之一。     |
| E2  | Configurable endurance profiles | Profiles like 24h light burn, 72h heavy burn, TBW-based goals; threshold-based pass/fail. 配置化耐久 Profile | High       | P2       | Phase 3–4 | Native                    | 需要在 E1 基础上抽象；定位“生产级 QA”。      |
| E3  | Trend analysis hooks            | Aggregate errors, throughput, health vs time; export summary & JSON for plotting. 耐久趋势分析               | Medium     | P2       | Phase 4   | Native                    | 和报告系统紧密结合；不必在最早版本就做完美。 |

---

### 1.6 Health & Metadata / 健康与元数据

| ID  | Feature                         | Description                                                                                                                              | Importance                                     | Priority | Sequence  | Strategy                           | Notes                                                                                                                |
| --- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | -------- | --------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| H1  | CID/CSD/EXT_CSD read & decode   | Use `mmc-utils` or native ioctl to fetch & decode manufacturer, product name, serial, date, capacity params. 读取并解析 CID/CSD/EXT_CSD  | High                                           | P0       | Phase 1   | Wrapper (mmc-utils) + later Native | 很快能给用户“可见价值”；实现难度适中。                                                                               |
| H2  | Vendor-specific health (sdmon)  | Wrap `sdmon` to obtain life-used %, power-on count, error counters, etc., for supported industrial cards. 工业卡健康信息（寿命百分比等） | High for industrial scenarios / Medium overall | P1       | Phase 2   | Wrapper (sdmon)                    | 复用 sdmon，避免自己啃 CMD56 协议。                                                                                  |
| H3  | Health snapshot with every test | Attach H1/H2 data to each run summary; detect health drift over time. 每次测试附带健康快照                                               | Medium                                         | P1       | Phase 2–3 | Native                             | 与日志/报告系统绑定，为趋势分析做准备。每个阶段（surface/performance/pipeline）都会记录 MMC+SDMON 快照并写入 JSONL。 |

---

### 1.7 Filesystem & Workload-level / 文件系统 & 工作负载级

| ID  | Feature                          | Description                                                                                               | Importance | Priority | Sequence  | Strategy | Notes                                             |
| --- | -------------------------------- | --------------------------------------------------------------------------------------------------------- | ---------- | -------- | --------- | -------- | ------------------------------------------------- |
| F1  | Filesystem integrity check hooks | Optionally run `fsck` / `chkdsk` pre/post test on test partition; capture results. 文件系统一致性检查集成 | Medium     | P2       | Phase 3   | Wrapper  | 用 hook 模式实现，避免强耦合；适合实验室/生产线。 |
| F2  | Small-file workload test         | Create/delete/read thousands of small files (rootfs/log-like). 大量小文件读写压力测试                     | Medium     | P2       | Phase 3–4 | Native   | 更贴近 Linux SBC / 路由器现实负载。               |
| F3  | Structured workload profiles     | Configurable test recipes combining FS ops + block-level ops. 结构化“工作负载组合测试”                    | Medium     | P3       | Phase 4+  | Native   | 完全可以作为高级特性，后期迭代。                  |

---

### 1.8 Image Flash & Verify / 镜像写入与校验

| ID  | Feature                                 | Description                                                                                                      | Importance | Priority | Sequence  | Strategy                         | Notes                                              |
| --- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ---------- | -------- | --------- | -------------------------------- | -------------------------------------------------- |
| I1  | Image write + verify                    | CLI subcommand to `dd`-like write an image and verify hash or byte-by-byte for selected regions. 镜像写入 + 校验 | Medium     | P2       | Phase 3   | Wrapper (`dd`/`cmp`/`sha256sum`) | 实用性强，但和“TF 卡 QA”核心略有距离，可中期实现。 |
| I2  | Integration with capacity/health checks | Option to run quick-test/health before & after flashing. 刷写前后自动做基础检查                                  | Medium     | P2       | Phase 3–4 | Native orchestration             | 以 pipeline 形式串联已有功能即可。                 |

---

### 1.9 Reporting, History & AI/Automation / 报告、历史与 AI/自动化

| ID  | Feature                                       | Description                                                                                                                                           | Importance | Priority | Sequence | Strategy | Notes                                      |
| --- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | -------- | -------- | -------- | ------------------------------------------ |
| R1  | Run summary report                            | Aggregate JSONL into concise per-run summary (capacity result, errors, health snapshot). 单次运行汇总报告                                             | High       | P0       | Phase 1  | Native   | 作为 `tfqa report` 的基础输出。            |
| R2  | Machine-readable JSON output for each command | Stable JSON schema for `--output json`; includes status, error_code, data, log_path. 面向自动化/AI 的 JSON 输出                                       | High       | P0       | Phase 1  | Native   | 早期定好 schema，后面尽量保持兼容。        |
| R3  | History & catalog                             | Optionally keep an index of runs (device, date, outcome) for later queries. 运行历史索引                                                              | Medium     | P2       | Phase 3  | Native   | 可以先用简单 SQLite/JSON 索引实现。        |
| R4  | Capabilities & describe for AI                | `tfqa capabilities` and `tfqa describe <cmd>` output machine-parseable schemas (args, options, destructive flag, requires_root). 面向 AI 的自描述能力 | High       | P0       | Phase 1  | Native   | 让 AI 能“自发现”如何调用、哪些是危险操作。 |

---

### 1.10 UX & Orchestration / 交互体验与编排

| ID  | Feature                               | Description                                                                                                                 | Importance | Priority | Sequence  | Strategy | Notes                                           |
| --- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ---------- | -------- | --------- | -------- | ----------------------------------------------- |
| U1  | Human CLI UX (help, progress, tables) | Clear subcommands, `--help`, colored output, progress bars, safe prompts. 人类友好 CLI 体验                                 | High       | P0       | Phase 0–1 | Native   | 直接关系到工具“可信度”和易用性。                |
| U2  | Non-interactive / batch / CI mode     | `--non-interactive`, `--yes`, `TFQA_MODE=ai` 等，保证脚本和 AI 调用不被卡在交互上。非交互/批处理模式                        | High       | P0       | Phase 0–1 | Native   | 你特别强调“AI 一等公民”，这点必须抢先做。       |
| U3  | Profile-based orchestration           | One command to run a sequence: detect → quick-test → full-test → health → report for a device / batch. Profile/测试方案编排 | Medium     | P2       | Phase 3   | Native   | 用 YAML/TOML 描述测试流水，生产环境很吃这一套。 |

---

## 2. Suggested Implementation Sequence / 建议实现顺序（按 Phase）

为了让项目既能快速 Demo，又保持长期扩展空间，建议按以下阶段推进。

### Phase 0 – Skeleton & Safety (P0)

**目标**：搭出安全的骨架，确保“不会误杀系统盘”，并且支持 AI/CI 友好的调用方式。

包含功能：

- C1 – Device discovery & classification
- C2 – Safety guardrails
- C3 – Configuration system
- C4 – Logging & JSONL event stream
- C5 – Capabilities detection
- U1 – Human CLI UX (basic 版)
- U2 – Non-interactive / batch / AI mode

**结果**：

- 有一个安全的 CLI 框架，能列设备、加载配置、写日志；
- 人类可以安全试用，AI/CI 也能“看懂”能力边界。

---

### Phase 1 – Core QA: Capacity + Health + Reporting (P0)

**目标**：把“真伪卡检测 + 基础健康 + 报告”打磨成可日常使用的核心功能。

包含功能：

- A1 – Quick capacity/authenticity test (wrapper `f3probe` + native fallback)
- A2 – Full-device capacity & integrity test (wrapper `f3write/f3read` + fallback)
- H1 – CID/CSD/EXT_CSD read & decode (mmc-utils wrapper)
- R1 – Run summary report
- R2 – JSON output schema for commands
- R4 – `capabilities` + `describe` for AI
- U1 – 完善帮助信息 & 错误信息

**结果**：

- 已经是一个可实际使用的“真伪卡 + 基础健康” QA 工具；
- 输出稳定 JSON，AI 可以可靠编排调用。

---

### Phase 2 – Surface, Perf, Industrial Health (P1)

**目标**：从“是否是假卡”扩展到“面子是否干净、速度是否靠谱、工业卡寿命如何”。

包含功能：

- S1, S2 – Surface scan (badblocks wrapper + F3 reuse)
- P1 – Basic sequential throughput
- P2 – Random I/O benchmark (fio wrapper + native summary)
- H2 – sdmon wrapper for industrial cards
- H3 – Health snapshot per test
- A3 – f3fix-style auto shrink（可选）

**结果**：

- 能够回答更多问题：
  - “有没有坏块？”
  - “速度是否达标？”
  - “工业卡健康度如何？”

---

### Phase 3 – Endurance, Workload, Flash (P2)

**目标**：走向“生产线可用”的 QA 平台，支持长时间耐久和现实工作负载。

包含功能：

- E1 – Simple burn-in
- E2 – Configurable endurance profiles
- F1 – FS integrity check hooks
- F2 – Small-file workload test
- I1 – Image write + verify
- U3 – Profile-based orchestration
- R3 – History & catalog

**结果**：

- 可以对批量卡做 24h/72h 等标准化 burn-in；
- 可以模拟真实“系统盘 / 日志盘 / 摄像机卡”等多种工作负载；
- 有基本历史索引和测试方案编排能力。

---

### Phase 4+ – Advanced Analytics & Nice-to-have (P3)

**目标**：在前面稳定基础上，增加高阶分析和更智能的 QA 体验。

可能包含：

- E3 – Trend analysis（随时间的错误率/吞吐/健康可视化）
  - 提供 `tfqa trends` 命令，对历史索引中的每个 stage 提取累计指标（吞吐、错误率等），支持 `--stage` 过滤和 JSON 输出，方便自动化对比回归。
- F3 – Structured workload combinations（复杂测试流水的声明式描述）
  - 让 automation 可以通过 `tfqa pipeline --stages <stage,...>` 精准调度（如 `--stages detect,quick-test,health`），JSON 输出包含阶段计划并写入历史记录。
- I2 – 自动前后检查（刷写前后自动跑 quick-test + health 对比）
- 更多可视化、远程 API、Web UI 等

---

## 3. Summary / 总结（直话直说）

- **必须一开始就做好的**：

  - 设备识别 & 安全护栏（C1, C2）、
  - 配置与日志（C3, C4）、
  - 能力探测 + AI 友好 CLI（C5, U1, U2, R2, R4）、
  - F3 + mmc-utils 的高质量封装（A1, A2, H1）。

- **第二梯队**：

  - 表面扫描（S1, S2）、
  - 顺序/随机性能测试（P1, P2）、
  - 工业卡健康（H2, H3）。

- **中长期关键价值点**：
  - 耐久/烧机（E1, E2, E3）、
  - 工作负载 Profile（P3, F2, F3, U3）、
  - 报告/趋势/历史（R1, R3）。

这些是让工具从“极客玩具”升级为“生产级 QA 平台”的核心差异化所在。

---

**End of document / 文档结束**
