# tfqa v0 Detailed Design  
tfqa v0 详细设计（设计要点 + 接口草图）

> 建议文件名：`docs/design-v0-details.md`  
> 本文是在 v0 架构的基础上，补充“设计要点 + 接口草图（API Sketch）”，为后续实现与评审提供统一参考。

---

## 0. Global Conventions / 全局约定

### 0.1 Typing & Models

- Python 3.11+，全项目统一使用类型标注。
- 数据结构尽量用 `pydantic.BaseModel`，便于：
  - JSON 序列化；
  - 校验；
  - AI / 自动化消费。
- 定义若干常用 type alias，统一语义。

```python
from __future__ import annotations
from typing import Literal, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime
from pathlib import Path

RunId = str        # e.g. "2025-11-18T10-15-30Z_9f3a21"
DevicePath = str   # e.g. "/dev/sdb"
```

---

## 1. `tfqa.core` – Core Infrastructure / 核心基建

### 1.1 `tfqa.core.models`

#### 设计要点

- 所有“跨模块共享”的核心模型集中在此模块。
- 强调 **schema 稳定性**，为：
  - CLI JSON 输出；
  - 报告与历史；
  - AI/自动化调用  
  提供统一结构。
- 核心模型：
  - `DeviceInfo`
  - `RunContext`
  - `TestConfig`
  - `TestResult` / `TestStatus`
  - `ToolCapability` / `Capabilities`

#### 接口草图

```python
# tfqa/core/models.py
from typing import Literal, Dict, List, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime

TestStatus = Literal["ok", "warning", "failed", "skipped", "error"]

class DeviceInfo(BaseModel):
    path: str                     # e.g. "/dev/sdb"
    name: str                     # e.g. "sdb"
    model: Optional[str] = None
    serial: Optional[str] = None
    size_bytes: int
    is_removable: bool
    is_system_disk: bool
    mountpoints: list[str] = []
    transport: Optional[str] = None  # "usb", "mmc", "nvme", etc.

class RunContext(BaseModel):
    run_id: str
    started_at: datetime
    device: DeviceInfo
    config_profile: str            # e.g. "default", "lab-heavy"
    destructive: bool
    mode: Literal["human", "ci", "ai"]
    extra_tags: Dict[str, str] = {}

class TestConfig(BaseModel):
    name: str                      # e.g. "capacity.quick"
    destructive: bool
    params: Dict[str, Any] = {}

class TestResult(BaseModel):
    name: str
    status: TestStatus
    started_at: datetime
    finished_at: datetime
    metrics: Dict[str, float] = {}     # MB/s, error_rate, etc.
    details: Dict[str, Any] = {}       # arbitrary structured detail
    logs_path: Optional[str] = None    # per-test log file / section
    error_code: Optional[str] = None   # stable code, if not ok
    warnings: list[str] = []

class ToolCapability(BaseModel):
    name: str                  # "f3probe", "mmc", "sdmon"
    available: bool
    version: Optional[str] = None
    path: Optional[str] = None
    notes: Optional[str] = None

class Capabilities(BaseModel):
    external_tools: Dict[str, ToolCapability]
    features: Dict[str, Literal["native", "wrapper", "disabled"]]
```

---

## 2. 包含更多设计内容（已略）…
