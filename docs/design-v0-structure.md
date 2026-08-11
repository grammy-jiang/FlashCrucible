# tfqa – v0 设计文档结构  
模块划分 + 包结构 + 外部依赖清单

> 建议文件名：`docs/design-v0-structure.md`  
> 本文定义 tfqa v0 的整体架构骨架，包括：模块划分、Python 包结构和外部依赖清单，为后续详细设计与实现提供统一参考。

---

## 1. 模块划分（Logical Modules）

### 1.1 CLI & UX 层（`tfqa.cli`）

**职责 / Responsibilities**

- 提供统一的命令行入口：`tfqa`
- 子命令解析、帮助信息、参数校验
- 人类友好输出（表格、进度条、提示）
- `--output {human,json}` / `--non-interactive` / `--yes` 等 UX 行为控制

**子模块建议**

- `tfqa.cli.main`  
  - 顶层入口（`python -m tfqa` / `tfqa`）
- `tfqa.cli.detect` – `tfqa detect`
- `tfqa.cli.quick_test` – `tfqa quick-test`
- `tfqa.cli.full_capacity` – `tfqa full-capacity-test`
- `tfqa.cli.health` – `tfqa health`
- `tfqa.cli.report` – `tfqa report`
- `tfqa.cli.capabilities` – `tfqa capabilities`
- `tfqa.cli.describe` – `tfqa describe <cmd>`
- `tfqa.cli.config_cmd` – `tfqa config show` / `tfqa config validate`

---

### 1.2 核心基建层（`tfqa.core`）

**职责**

- 设备发现 & 安全判断
- 配置加载与合并（CLI / 环境变量 / 配置文件）
- 日志 & 事件流（JSONL）
- 能力探测（外部工具存在性、版本等）
- 公共数据模型（`run_id`、`device`、`result` schema）

**子模块**

- `tfqa.core.devices`  
  - 扫描 `/sys/block`、调用 `lsblk` / `blkid` 或 `psutil.disk_partitions()`
  - 判断 removable、system disk、挂载点等
- `tfqa.core.safety`  
  - 设备安全策略（是否允许破坏性操作）
  - 高层接口：`assert_safe_for_destructive(device, flags)`
- `tfqa.core.config`  
  - 配置层级：defaults → /etc → ~/.config → ./tfqa.toml → env → CLI
  - 解析 TOML（v0 建议 TOML 为主）
- `tfqa.core.logging`  
  - 标准 logging + JSONL per run
  - 管理 `log_dir`、生成 `run_id`、日志文件命名
- `tfqa.core.capabilities`  
  - 探测 `f3write/f3read/f3probe`、`mmc`、`sdmon`、`badblocks`、`fio`、`dd`、`fsck` 等
  - 缓存结果并提供结构化对象（供 CLI & AI 调用）
- `tfqa.core.models`  
  - `DeviceInfo`, `RunContext`, `TestResult`, `CapabilityInfo` 等数据类
- `tfqa.core.errors`  
  - 统一异常 & 错误码定义
  - 映射到 CLI/JSON 的 `status` + `error_code`

---

### 1.3 测试引擎层（`tfqa.tests`）

**职责**

- 各类测试的**核心逻辑实现**（不处理 CLI/输出）
- 输入：`RunContext` + `TestConfig`
- 输出：`TestResult` + 事件流（写入 `tfqa.core.logging`）

**子模块**

- `tfqa.tests.capacity.quick`  
  - 快速容量/真伪检测：优先调用 F3（`f3probe`），否则走自研抽样写读
- `tfqa.tests.capacity.full`  
  - 全盘破坏性写读：优先 F3（`f3write` + `f3read`），否则自研 sequential 流程
- `tfqa.tests.surface.scan`  
  - 表面坏块扫描：封装 `badblocks` 或自研多模式
- `tfqa.tests.performance.basic`  
  - 简单顺序读写基准（1M/4M 读写）
- `tfqa.tests.performance.random`  
  - 随机 IO 基准：优先 `fio` wrapper，fallback 自研简单模式
- `tfqa.tests.endurance.simple`  
  - 基础耐久 / 烧机循环（v0 做简单 profile 即可）
- `tfqa.tests.health.snapshot`  
  - 基于 `mmc-utils` + `sdmon` 生成健康快照
- `tfqa.tests.fs.smallfiles`（Phase 3+）  
  - 小文件 workload / FS 压力测试

---

### 1.4 外部工具封装层（`tfqa.ext`）

**职责**

- 对系统二进制工具做**薄封装**，提供统一 Python API：
  - 执行命令
  - 解析 stdout/stderr
  - 标准化错误处理和超时控制

**子模块**

- `tfqa.ext.f3`  
  - `run_f3probe`, `run_f3write`, `run_f3read`, `run_f3fix`
- `tfqa.ext.mmc`  
  - `read_cid`, `read_csd`, `read_ext_csd`，解析为结构化模型
- `tfqa.ext.sdmon`  
  - `read_health`（返回寿命百分比、通电次数等）
- `tfqa.ext.badblocks`  
  - `run_badblocks_readonly`, `run_badblocks_write`
- `tfqa.ext.fio`  
  - `run_fio_job(job_config)`，解析 JSON 或文本输出
- `tfqa.ext.dd` / `tfqa.ext.fsck`（后期用于 I1/F1）
  - `run_dd_image_write`, `run_cmp`, `run_sha256sum`
  - `run_fsck` 钩子

---

### 1.5 报告与历史层（`tfqa.reporting`）

**职责**

- 将某次 run 的 JSONL 事件流汇总为 Summary
- 可选：维护全局的历史索引（设备、时间、结果……）
- 为 CLI/AI 提供 `tfqa report` / 历史查询输出

**子模块**

- `tfqa.reporting.summary`  
  - 从 `run_id` 对应 JSONL 生成 Summary 对象：
    - 容量/真伪测试结果
    - 错误统计
    - 健康快照
    - 性能摘要
- `tfqa.reporting.history`  
  - 维护全局 runs 索引（简单 JSON 或 SQLite）
  - 支持按设备、时间范围查询
- `tfqa.reporting.formatters`  
  - human/human-rich/json 格式转换
  - 表格/颜色 & JSON schema 对应

---

### 1.6 编排与 Profile 层（`tfqa.orchestration`）

**职责**

- 将多个测试步骤编排成一个 pipeline（测试方案）
- 读取 Profile（TOML/YAML）描述复合测试流程

**子模块**

- `tfqa.orchestration.pipeline`  
  - 统一执行流水，例如：  
    - detect → quick-test → full-capacity-test → health → report
  - 处理失败策略（fail-fast / continue-on-error）
- `tfqa.orchestration.profiles`  
  - 加载/验证 profile 文件，生成 `PipelineConfig`
  - 示例：`default`, `lab-heavy`, `burnin-24h`

> v0 可以只提供 minimal pipeline；复杂 profile 设计可以留到 Phase 3。

---

### 1.7 AI Interface 考量（横切关注点）

> 不单独建包，但会影响 `cli` / `core` 的接口设计。

- 所有 CLI 子命令要支持：
  - `--output json`（稳定 schema）
  - `--non-interactive`（无交互确认）
- `tfqa.cli.describe`：
  - 输出命令参数/选项/是否破坏性/是否需要 root 的自描述 JSON
- `tfqa.core.capabilities`：
  - 输出当前可用的 feature + 实现方式（wrapper/native/disabled）
- `tfqa.core.errors`：
  - 保持 `status` + `error_code` 的稳定枚举，方便 AI 判断重试 / 放弃 / 升级处理

---

## 2. 包结构（Python Package Layout）

建议基础目录结构如下（仓库根目录视为项目根）：

```text
tfqa/
  __init__.py

  cli/
    __init__.py
    main.py
    detect.py
    quick_test.py
    full_capacity.py
    health.py
    report.py
    capabilities.py
    describe.py
    config_cmd.py

  core/
    __init__.py
    devices.py
    safety.py
    config.py
    logging.py
    capabilities.py
    models.py
    errors.py
    utils.py

  tests/
    __init__.py
    capacity/
      __init__.py
      quick.py
      full.py
    surface/
      __init__.py
      scan.py
    performance/
      __init__.py
      basic.py
      random.py
    endurance/
      __init__.py
      simple.py
    health/
      __init__.py
      snapshot.py
    fs/
      __init__.py
      smallfiles.py   # Phase 3+

  ext/
    __init__.py
    f3.py
    mmc.py
    sdmon.py
    badblocks.py
    fio.py
    dd.py
    fsck.py

  reporting/
    __init__.py
    summary.py
    history.py
    formatters.py

  orchestration/
    __init__.py
    pipeline.py
    profiles.py

  data/
    __init__.py
    schemas/
      __init__.py
      json/
      toml/
    profiles/
      default.toml
      lab-heavy.toml

tests/
  test_cli_*.py
  test_core_*.py
  test_ext_*.py
  test_tests_*.py
  test_reporting_*.py
```

说明：

- `tfqa/` 为主包，包含所有运行时代码；
- 顶层 `tests/` 为 Pytest 单测目录，按子系统分文件；
- `data/schemas/` 预留给 JSON/TOML schema 定义（用于配置和输出验证）；
- `data/profiles/` 放置内置测试 profile 示例。

---

## 3. 外部依赖清单（Python 依赖 + 系统二进制）

### 3.1 Python 依赖（v0 核心）

**必需 / Core**

- CLI 框架：
  - `typer`（推荐）或 `click`
- 配置解析：
  - Python 3.11+：标准库 `tomllib`
  - 更低版本：`tomli`（只读）
- 数据模型 / 校验：
  - `pydantic`（v2 系列）用于 `TestResult`, `DeviceInfo`, `Capabilities` 等
- 终端美化（强烈建议）：
  - `rich`（表格、进度条、颜色、traceback）
- 用户目录 & 路径：
  - `platformdirs`（统一确定 log/config 路径）

**可选 / Phase 2+**

- `pyyaml` – 若需要支持 YAML Profile
- `sqlalchemy` + SQLite – 若历史索引要做得更复杂
- `tabulate` – 如果不用 rich 的表格功能，但一般 rich 足够

---

### 3.2 系统二进制依赖（Wrapped Tools）

> 工具不存在时，需要在 `tfqa.core.capabilities` 中显式标记，并自动降级到自研实现或禁用相关功能。

**优先级高（v0/v1 就要考虑封装）**

- `f3write` / `f3read` / `f3probe` / `f3fix`  
  - 功能：容量 & 假卡检测、缩容修复
- `mmc`（mmc-utils）  
  - 功能：CID/CSD/EXT_CSD 读取与解析
- `sdmon`  
  - 功能：工业/high-endurance SD 卡健康状态（寿命百分比、通电次数等）

**优先级中（Phase 2+）**

- `badblocks`  
  - 功能：坏块扫描（只读/写读）
- `fio`  
  - 功能：复杂 I/O workload、性能 + 耐久测试
- `dd` / `cmp` / `sha256sum`  
  - 功能：镜像写入与校验（Image Flash & Verify）

**优先级低 / 辅助**

- `lsblk` / `blkid` / `udevadm`  
  - 用于辅助获取设备信息（若 `psutil` 足够，可选）
- `fsck.*` / `chkdsk`（平台相关）  
  - 文件系统一致性检查 hook（用于 FS-level 测试）

---

## 4. 总结 / Summary

- 本设计结构将 tfqa 分为：CLI/UX、核心基建、测试引擎、外部封装、报告历史、编排 Profile 六大层级；
- Python 包结构与模块划分一一对应，便于：
  - 单元测试组织；
  - 未来拆分子项目（如单独发布 `tfqa-core`、`tfqa-ext` 等）；
  - 对 AI/自动化暴露清晰、稳定的调用面。
- 外部依赖策略是：
  - **能封装的尽量封装（F3、mmc-utils、sdmon、fio、badblocks）**；
  - **能力探测与降级逻辑**集中在 `tfqa.core.capabilities`；
  - **核心安全与 UX 逻辑由自研模块掌控**（不依赖外部工具）。

后续可以在本结构基础上，为每个关键模块补充：

- 主要类与函数签名草图；
- 数据模型字段定义；
- 典型调用流程序列图；  

这部分建议放入 `design-v0-details.md` 作为下一步工作。
