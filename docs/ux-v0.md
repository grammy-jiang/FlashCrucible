
# tfqa – CLI UX & AI-Oriented UX Design  
人类友好 CLI 体验 & 面向 AI 的交互设计

> 建议文件名：`docs/ux-v0.md`  
> 与 `overview-v0.md`、`design-v0-structure.md` 一起保存。  
> 本文专注于：**人类用户的 CLI 体验设计** 和 **AI 作为一等公民用户时的交互规范**。

---

## 1. UX Goals / 交互设计目标

### 1.1 High-level UX Goals / 高层 UX 目标

**English**

- Make `tfqa` safe to use on machines that matter (routers, Pis, servers).
- Make it **predictable** and **scriptable**.
- Make it easy for both:
  - a human on a terminal,
  - and an AI agent orchestrating tests in CI / MCP / automation.

**中文**

- 确保 `tfqa` 在“关键机器”（路由器、树莓派、服务器）上使用时安全可控；
- 行为**可预测**、**易脚本化**，不会搞“黑魔法”；
- 对两类用户都友好：
  - 终端里的真人；
  - 负责编排测试的 AI 代理（CI / MCP / 自动化）。

---

## 2. Human-Friendly CLI UX / 面向人类的 CLI 体验

### 2.1 Command Model / 命令模型

**Principles / 原则**

- **Single-responsibility subcommands**  
  Each command does one thing and does it clearly:
  - `detect` – list and inspect devices.
  - `quick-test` – fast, mostly non-destructive capacity/authenticity check.
  - `full-capacity-test` – destructive full-span write+verify.
  - `health` – read card metadata and health info.
  - `report` – aggregate results for a given run.
  - `config show` – show effective configuration.
  - `capabilities` – show which features and external tools are available.
  - `describe` – describe how to use a particular subcommand.

- **No hidden modes**  
  No completely different behavior based on host model / card vendor etc.  
  Everything must be controllable via flags / config.

**Example / 示例**

```bash
# List devices
tfqa detect

# Quick authenticity test (non-destructive by default)
tfqa quick-test --device /dev/sdb

# Full destructive capacity test (requires confirmation)
tfqa full-capacity-test --device /dev/sdb

# Show health info (CID/CSD/EXT_CSD)
tfqa health --device /dev/mmcblk0

# Show last-run summary
tfqa report

# Show all capabilities & external tool status
tfqa capabilities
```

---

### 2.2 Global Options & Behavior / 全局选项与行为

**Global flags / 全局标志**

- `--output {human,json}`  
  - Default: `human`  
  - `human`: formatted tables, progress, colored text (if TTY).  
  - `json`: structured JSON object to stdout (one per invocation).

- `--non-interactive`  
  - Disable all prompts.  
  - If a command normally requires confirmation, either:
    - require `--yes`, or  
    - fail with a clear error.

- `--yes`  
  - Skip confirmations for destructive commands.  
  - Meant for scripted usage (human or CI), not for casual manual use.

- `--dry-run`  
  - Show what would be done (device, operations, estimated time), but **do not actually perform** I/O.

- `--no-color`  
  - Disable colored output and special formatting.

- `-v/--verbose` / `-q/--quiet`  
  - Control chatter.  
  - Verbose: more detail per phase.  
  - Quiet: only key milestones / warnings / errors.

**Environment variables / 环境变量**

- `TFQA_MODE=ai`  
  - Equivalent to enabling:
    - `--non-interactive`
    - `--output json`
    - `--no-color`
  - For AI / automation contexts.

- `TFQA_NON_INTERACTIVE=1`  
  - Same semantics as `--non-interactive` (CLI flag overrides env).

---

### 2.3 Safety UX / 安全相关体验设计

**Principles / 原则**

1. **No default device**  
   - Users must specify `--device` or explicit positional parameter.
   - No “just use the last device” or guessing based on size.

2. **System disk & mounts protection**  
   - If a device looks like the system disk (root filesystem, `/boot`, etc.):
     - Disallow destructive operations by default.
   - If a device is mounted:
     - For destructive operations: fail unless `--force` and `--yes` and `--non-interactive` (or explicit double-confirm in interactive mode).

3. **Loud about destructive operations**  
   - For `full-capacity-test`, `burn-in` (future), etc.:
     - Print a clear warning banner:
       - Device path
       - Size
       - Mounts
       - That **all data will be destroyed**.

**Example interactive confirmation / 交互式确认示例**

```text
$ tfqa full-capacity-test --device /dev/sdb

[WARN] You are about to run a DESTRUCTIVE test on /dev/sdb
       All data on this device WILL BE LOST.

  Path          : /dev/sdb
  Size          : 119.2 GiB
  Model         : SanDisk Ultra
  System disk   : NO
  Mounted       : /media/usb (vfat)

Type YES to continue, or anything else to abort: 
```

- If the user does **not** type `YES` (case-sensitive), abort cleanly.

---

### 2.4 Help & Discoverability / 帮助与可发现性

**`--help` behavior / `--help` 行为**

- Every subcommand must provide:
  - Clear description.
  - Common usage examples.
  - Explanation of destructive vs non-destructive behavior.

**Example / 示例**

```bash
tfqa quick-test --help
```

Should include:

- Short description:  
  > “Quick capacity/authenticity check using F3 or native sampling.  
  > Defaults to non-destructive mode (tests free space only).”

- Example usage:
  - `tfqa quick-test --device /dev/sdb`
  - `tfqa quick-test --device /dev/mmcblk0 --free-space-only`

---

### 2.5 Human-Oriented Output / 面向人类的输出

**Principles / 原则**

- **High signal, low noise**:
  - Show phases: “Preparing”, “Write pass”, “Verify pass”, “Summary”.
  - Show ETA where possible.
- **Use tables for lists / summary**:
  - Device lists, capability lists, summary metrics.
- **Progress**:
  - Use progress bars when TTY is available.
  - On non-TTY, emit periodic “X% complete” messages rather than bars.

**Example – `detect` human output**

```text
$ tfqa detect

Detected block devices:

+------+-----------+-----------+------------+-------------+-----------+
| ID   | Path      | Size      | Model      | Vendor      | Removable |
+------+-----------+-----------+------------+-------------+-----------+
| dev1 | /dev/sda  | 512.0 GiB | Samsung SSD| Samsung     | No        |
| dev2 | /dev/sdb  | 119.2 GiB | Ultra      | SanDisk     | Yes       |
| dev3 | /dev/mmcblk0 | 29.7 GiB | SD16G    | Generic     | Yes       |
+------+-----------+-----------+------------+-------------+-----------+

Hint: Use "tfqa quick-test --device /dev/sdb" to verify a removable card.
```

---

## 3. AI-Oriented UX / 面向 AI 的交互设计

### 3.1 Design Goals / 设计目标

**English**

- AI should never have to “guess”:
  - about flags,
  - about whether a command is destructive,
  - about output structure.
- Everything AI needs should be discoverable through:
  - JSON output,
  - `capabilities`,
  - `describe`.

**中文**

- AI 不应该凭空猜测：
  - 不猜参数；
  - 不猜命令是否破坏性；
  - 不猜输出结构。
- 所有 AI 需要的信息都应通过：
  - JSON 输出、
  - `capabilities`、
  - `describe`  
  这三个渠道“自描述”给出。

---

### 3.2 AI Global Flags & Modes / AI 全局选项与模式

**Recommended pattern for AI / AI 推荐调用模式**

- Always set:
  - `--output json`
  - `--non-interactive`
  - `--no-color`
- Optionally:
  - `--dry-run` for planning.
- Or simply set environment:
  - `TFQA_MODE=ai`

**Example / 示例**

```bash
# For AI: ensure machine-safe behavior
TFQA_MODE=ai tfqa detect
TFQA_MODE=ai tfqa quick-test --device /dev/sdb
TFQA_MODE=ai tfqa full-capacity-test --device /dev/sdb --yes
```

---

### 3.3 JSON Output Contract / JSON 输出约定

**Top-level JSON structure / 顶层结构**

For all commands with `--output json`, stdout should emit **one JSON object**:

```json
{
  "status": "ok" | "fail" | "error" | "aborted",
  "command": "quick-test",
  "run_id": "20250218-103012-xyz123",
  "device": {
    "path": "/dev/sdb"
  },
  "error_code": null,
  "message": "Quick capacity test completed successfully.",
  "data": { /* command-specific payload */ },
  "log_path": "/home/user/.tfqa/logs/run-20250218-103012-xyz123.jsonl"
}
```

**Fields / 字段说明**

- `status`  
  - `"ok"`: command executed as expected; thresholds satisfied.  
  - `"fail"`: command executed, but card failed tests (fake capacity, too many errors, etc.).  
  - `"error"`: command could not complete due to environment/config issues.  
  - `"aborted"`: user/AI aborted or forced stop.

- `command`  
  - The subcommand name (`"detect"`, `"quick-test"`, etc.).

- `run_id`  
  - Present for commands that perform tests (`quick-test`, `full-capacity-test`, `health` etc.);  
  - Optional or null for pure informational commands like `capabilities`.

- `device`  
  - At minimum: `{ "path": "/dev/sdb" }`, when relevant.

- `error_code`  
  - `null` when `status="ok"` or `"fail"`;
  - A machine-usable string when `status="error"` (see below).

- `message`  
  - Short, human-readable description (English, concise).

- `data`  
  - Command-specific payload (see examples below).

- `log_path`  
  - Path to JSONL log file for this `run_id` (tests only).

---

### 3.4 Error Model for AI / 面向 AI 的错误模型

**Exit codes / 退出码**

- `0` – Success (`status="ok"` or `"fail"`).
- `1` – Test completed, card failed (quality problem).
- `2` – Configuration or argument error (`status="error"`).
- `3` – Environment/system error (I/O, missing tools, permissions).
- `130` – Interrupted (Ctrl+C / SIGINT, etc., `status="aborted"`).

**`error_code` taxonomy / `error_code` 分类**

Examples:

- `INVALID_ARGUMENT` – bad CLI arguments.
- `DEVICE_NOT_FOUND` – `--device` path not recognized.
- `DEVICE_UNSAFE` – system disk or mounted when destructive test requested.
- `NO_ROOT_PERMISSION` – insufficient privileges.
- `EXT_TOOL_MISSING` – required external tool (e.g., `f3write`) not found.
- `RUNTIME_IO_ERROR` – underlying read/write failure.
- `INTERNAL_ERROR` – unexpected exception / bug.

**Example / 示例**

```json
{
  "status": "error",
  "command": "full-capacity-test",
  "run_id": null,
  "device": { "path": "/dev/sda" },
  "error_code": "DEVICE_UNSAFE",
  "message": "Refusing to run destructive test on a likely system disk.",
  "data": {
    "is_system_disk": true,
    "mounted": [
      { "mountpoint": "/", "fstype": "ext4" },
      { "mountpoint": "/boot", "fstype": "vfat" }
    ]
  },
  "log_path": null
}
```

AI can:

- See `error_code="DEVICE_UNSAFE"` and never retry this command with `/dev/sda`.
- Possibly decide to prompt a human or choose another device.

---

### 3.5 `capabilities` – Feature & Tools Discovery / 能力与工具发现

**Purpose / 目的**

- Allow AI (and humans) to discover:
  - what tests are available,
  - which implementations are in use (native vs wrapper),
  - and which external tools are installed.

**Example JSON / 示例**

```bash
tfqa capabilities --output json
```

```json
{
  "status": "ok",
  "command": "capabilities",
  "run_id": null,
  "device": null,
  "error_code": null,
  "message": "Capabilities probe successful.",
  "data": {
    "version": "0.1.0",
    "features": {
      "capacity_quick": { "available": true, "mode": "hybrid", "tools": ["f3probe"] },
      "capacity_full":  { "available": true, "mode": "hybrid", "tools": ["f3write", "f3read"] },
      "health":         { "available": true, "mode": "wrapper", "tools": ["mmc", "sdmon"] },
      "performance":    { "available": false, "mode": "none", "tools": [] },
      "endurance":      { "available": false, "mode": "none", "tools": [] }
    },
    "ext_tools": {
      "f3probe": { "found": true, "path": "/usr/bin/f3probe", "version": "8.0" },
      "f3write": { "found": true, "path": "/usr/bin/f3write", "version": "8.0" },
      "mmc":     { "found": true, "path": "/usr/bin/mmc", "version": "0.1" },
      "sdmon":   { "found": false }
    }
  },
  "log_path": null
}
```

**AI usage / AI 使用策略**

- Call `capabilities` **once** at the start of a session.
- Decide:
  - Whether to use `quick-test` and/or `full-capacity-test`.
  - Whether health info will be available.
  - Whether to expect wrapper vs native behavior.

---

### 3.6 `describe` – Command Schema Introspection / 命令参数自描述

**Purpose / 目的**

- Let AI discover:
  - what arguments and options a command expects,
  - which are required,
  - whether it is destructive,
  - whether it requires root.

**Example / 示例**

```bash
tfqa describe full-capacity-test --output json
```

```json
{
  "status": "ok",
  "command": "describe",
  "run_id": null,
  "device": null,
  "error_code": null,
  "message": "Command schema.",
  "data": {
    "name": "full-capacity-test",
    "summary": "Run a destructive full-span write+verify test to detect fake capacity and bad sectors.",
    "destructive": true,
    "requires_root": true,
    "arguments": [
      {
        "name": "device",
        "type": "string",
        "required": true,
        "position": 1,
        "description": "Block device path, e.g. /dev/sdb"
      }
    ],
    "options": [
      {
        "name": "--yes",
        "type": "bool",
        "required": false,
        "default": false,
        "description": "Skip confirmation prompts. Use with care."
      },
      {
        "name": "--profile",
        "type": "string",
        "required": false,
        "default": "default",
        "allowed_values": ["default", "slow-host"],
        "description": "Adjust I/O pattern for slow hosts."
      }
    ]
  },
  "log_path": null
}
```

**AI flow / AI 典型流程**

1. `tfqa describe full-capacity-test --output json`
2. See `destructive=true`, `requires_root=true`.
3. Only build a command if:
   - it can tolerate data loss;
   - it knows which device to use (from `detect` and human/upper-layer policy).

---

### 3.7 Per-Command JSON Payload Examples / 各命令 JSON 负载示例

#### 3.7.1 `detect --output json`

```json
{
  "status": "ok",
  "command": "detect",
  "run_id": null,
  "device": null,
  "error_code": null,
  "message": "3 block devices detected.",
  "data": {
    "devices": [
      {
        "id": "dev1",
        "path": "/dev/sda",
        "size_bytes": 512110190592,
        "removable": false,
        "model": "Samsung SSD",
        "vendor": "Samsung",
        "is_system_disk": true,
        "mounted": [
          { "mountpoint": "/", "fstype": "ext4" }
        ]
      },
      {
        "id": "dev2",
        "path": "/dev/sdb",
        "size_bytes": 128035676160,
        "removable": true,
        "model": "SanDisk Ultra",
        "vendor": "SanDisk",
        "is_system_disk": false,
        "mounted": []
      }
    ]
  },
  "log_path": null
}
```

AI can choose `/dev/sdb` as a candidate for testing.

---

#### 3.7.2 `quick-test --output json --non-interactive`

```json
{
  "status": "ok",
  "command": "quick-test",
  "run_id": "20250218-104522-abc789",
  "device": {
    "path": "/dev/sdb"
  },
  "error_code": null,
  "message": "Quick capacity test completed successfully.",
  "data": {
    "ext_tool_used": "f3probe",
    "fake_capacity_detected": false,
    "estimated_real_size_bytes": 128035676160,
    "coverage_percent": 90.1,
    "duration_seconds": 432.5
  },
  "log_path": "/home/user/.tfqa/logs/run-20250218-104522-abc789.jsonl"
}
```

If fake capacity is detected:

```json
{
  "status": "fail",
  "command": "quick-test",
  "run_id": "20250218-110233-xyz555",
  "device": {
    "path": "/dev/sdb"
  },
  "error_code": null,
  "message": "Quick capacity test indicates a likely fake or over-reported card.",
  "data": {
    "ext_tool_used": "f3probe",
    "fake_capacity_detected": true,
    "estimated_real_size_bytes": 15931539456,
    "coverage_percent": 92.3,
    "duration_seconds": 215.0
  },
  "log_path": "/home/user/.tfqa/logs/run-20250218-110233-xyz555.jsonl"
}
```

---

#### 3.7.3 `health --output json`

```json
{
  "status": "ok",
  "command": "health",
  "run_id": "20250218-111010-hlth01",
  "device": {
    "path": "/dev/mmcblk0"
  },
  "error_code": null,
  "message": "Health information retrieved successfully.",
  "data": {
    "source": "mmc-utils+sdmon",
    "manufacturer": "Samsung",
    "product_name": "MB-MJ64",
    "serial": "0x12345678",
    "manufacture_date": "2025-01-15",
    "life_used_percent": 3,
    "power_on_count": 21,
    "raw": {
      "cid": "xxxx",
      "csd": "yyyy",
      "ext_csd": "...."
    }
  },
  "log_path": "/home/user/.tfqa/logs/run-20250218-111010-hlth01.jsonl"
}
```

---

## 4. Config & Environment for UX / 配置与环境对 UX 的影响

**Config precedence / 配置优先级**

- `CLI > ENV > Local config (./tfqa.toml) > User config (~/.config/tfqa/config.toml) > System config (/etc/tfqa/config.toml) > Defaults`

**UX-relevant config keys / 关键 UX 配置项示例**

- `ui.default_output` – `"human"` or `"json"`.
- `ui.default_mode` – `"human"` or `"ai"`.
- `ui.confirm_destructive` – `true/false`（交互模式下是否强制确认）。
- `logging.log_dir` – 日志目录。
- `tests.default_profiles` – quick/standard/lab 等默认阈值。

**`tfqa config show` behavior / `tfqa config show` 行为**

- 应打印：
  - 最终生效配置（可用 YAML/TOML 风格）；
  - 每个 key 的来源：
    - `[default]`、`[env]`、`[user config]`、`[system config]`、`[cli]`。

---

## 5. Recommended Interaction Patterns / 推荐交互模式

### 5.1 For Humans / 对人类用户

典型工作流：

```bash
# 1. 识别正确的设备
tfqa detect

# 2. 对新插入的卡做非破坏性快速检查
tfqa quick-test --device /dev/sdb

# 3. 若是全新且可牺牲的卡，再做破坏性全盘测试
tfqa full-capacity-test --device /dev/sdb

# 4. 查看健康信息
tfqa health --device /dev/sdb

# 5. 查看汇总报告
tfqa report --run-id 20250218-104522-abc789
```

---

### 5.2 For AI / 对 AI 代理

典型工作流：

```bash
# 1. 发现能力
TFQA_MODE=ai tfqa capabilities

# 2. 查询关键命令的 schema
TFQA_MODE=ai tfqa describe detect
TFQA_MODE=ai tfqa describe quick-test
TFQA_MODE=ai tfqa describe full-capacity-test

# 3. 列出设备
TFQA_MODE=ai tfqa detect --output json

# 4. 解析 JSON，选择一个非系统、未挂载、removable 的设备

# 5. 运行 quick-test
TFQA_MODE=ai tfqa quick-test --device /dev/sdb --output json --non-interactive

# 6. 如被允许且卡可牺牲，再运行 full-capacity-test
TFQA_MODE=ai tfqa full-capacity-test --device /dev/sdb --yes --output json --non-interactive

# 7. 拉取报告
TFQA_MODE=ai tfqa report --run-id <run_id> --output json
```

AI 逻辑应始终：

- 检查 `status` 和 `error_code`；
- 避免在 `DEVICE_UNSAFE` 等错误上反复重试破坏性命令；
- 优先选择非破坏性测试，除非上层策略明确要求更激进测试。

---

**End of document / 文档结束**
