---
name: craft-anything
description: 用 Craft Anything（纯 Rust 逆向工作台，302+ 工具）做静态逆向：自带 ELF/PE/Mach-O/DEX/APK/ASAR/plist/.NET/Go/Unity/Unreal/SQLite/pcap 解析器与伪 C 反编译器，不依赖 Ghidra/JADX；也可净室外观察并驱动编码 agent 重建。与 reverse-index（发现）、reverse-skill（case/证据）、REA（JS 语义）协同。用于「看这个二进制/APK/Electron 应用/游戏文件是怎么做的」。
---

# craft-anything — 自带引擎的逆向工作台

本机安装：`craft-anything-cli`（v0.5.0，SHA256 与官方 release 一致）。
GUI 版在 `<CRAFT_ANYTHING_INSTALL>/craft-anything.exe`。
MCP 已注册到 `~/.reasonix/config.json`（`craft-anything=<路径> mcp`），重启 Reasonix 后生效。

## 何时用

- **静态看结构**：PE/ELF/Mach-O 的函数、xref、调用图、伪 C、字符串、节区、PE 资源、签名；
- **移动端静态**：APK/DEX 解析、资源、Flutter 识别；
- **Electron/JS**：`app.asar` 解包、模块图、IPC 通道映射、bundle 还原可读源码、source map 定位；
- **游戏文件**：Unity bundle/序列化对象、Unreal pak/IoStore、纹理导出；
- **其他格式**：SQLite 文件、pcap 抓包、固件、Java/JAR（Vineflower）、.NET 结构；
- **净室外观察**：像访客一样看网站/窗口/手机屏，产出「外部可见面」证据；
- **重建**：study → build → check → refine，用截图 SSIM 收敛（**本机用 DeepSeek 后端，已实测可用，不需要 Claude Code；见下**）。

## 何时不要用

- 要**深度反编译**（类型恢复、精确交叉引用）→ 用 Ghidra（Craft Anything 的伪 C 是寄存器级、无类型恢复，只能当骨架读）；
- **目标是 RISC-V（或任何非 x86/ARM 架构）→ 直接用 Ghidra，别绕 Craft Anything**。实测：`--architecture` 只认 `x86/x86_64/arm/arm64`；`--language-id RISCV:LE:32:default` 不能单独生效（报 `raw-binary needs architecture`）；用 `arm` 占位能跑但走的是**自研引擎**（只跟到 entry 的 19 条指令、1 个函数），**根本没走 Ghidra 桥**。
- 要 **Electron 语义图/IPC 深挖**→ REA 更成熟；
- 要**运行时注入/脱壳/动态调试**→ Frida/GDB（本机已装）；
- 找「有没有人做过」→ 先用 `reverse-index`。

## 能力校准：Interop-Bench（上游 bench/）

`awesome-reverse-engineered` 自带一个基准，用来客观衡量「能否用样本文件/流量/活设备写出能工作的互操作代码」。它比宣传语可信的地方：

- 任务由**随机种子生成**，答案不存在于任何地方，不可记忆；
- **代码跑分**：解码器读没见过的文件、客户端改没见过的设备状态，**没有 LLM 评判**；
- **oracle 自身被测试**：参考解 100%、空提交 0 分、重放样本失败；
- 纯 Python 标准库，本机可跑。已实测：`bench/tests` 14 项全过，`reference` 在 fmt L1 与 proto L5 均 100%。

**分级是现成的逆向难度标尺**：

| Level | Format（只有文件） | Protocol（有活设备） |
|---|---|---|
| 1 | 定长记录 | 明文帧 + 校验和 |
| 2 | TLV + varint | 长度前缀、autokey XOR、序列号 |
| 3 | 定义/数据消息（类 Garmin FIT） | Hello、会话 XOR、CRC（类小米 miIO） |
| 4 | L3 压缩混淆、delta 时间、字符串表 | 无标注抓包、分片、自定义 CRC |
| 5 | 写 hub 能接受的文件（自定义 CRC） | L4 + 校验**只以 RISC-V 机器码形式存在于固件里** |

怎么用：

```powershell
# 校 oracle（不需 agent/key）
python bench\run.py one --agent reference --track proto --level 5
# 给自己的组合打分（需要可执行的 agent 命令）
python bench\run.py matrix --agents 'cmd:your-agent {prompt}' --seeds 3 --out mine.jsonl
# 手工做题：铺好 job 后自己写 client.py，放 DONE 即评分
python bench\run.py serve --agents me --tracks proto --levels 5 --seeds 1 --root jobs --out out.jsonl --minutes 40
```

**本机实测记录（2026-11-08，L5 protocol，未解出）**：独立解出了帧结构（`magic|seq|cmd|len|payload|checksum`，105 帧零例外）、命令码规律（响应 = 请求 | 0x80）、payload 明文；固件熵 7.88/8.0 不是裸码，扫出 5 段内嵌 RISC-V 函数（0x400–0x678），反汇编出混合函数（乘法+双旋转），但用 105 个样本验证 12 种组合**全部不匹配** —— 固件里 4 个结构相似的混合函数本身就是干扰项。完整记录在 `<WORK>/ib-jobs/me-proto5-0\REVERSE_NOTES.md`。

**这条记录的意义**：L5 刻意做到「没有工具能直接给答案」——Craft Anything 不认 RISC-V、Ghidra 在无符号裸 RV32 上自动分析找到 0 个函数。想通过，必须自己扫代码段、逐条读指令。这正是能力校准该暴露的东西。


## 三种接入方式

### 1) CLI（最直接）

```powershell
$ca = 'craft-anything-cli'
& $ca commands                                   # 321 个工具（按 toolset 分组）
& $ca doctor                                     # 安装/引擎/agent/MCP 自检
& $ca capabilities                               # 本机可用工具面
& $ca --deep triage "C:\path\target.exe" --project D:\work\proj-x
& $ca --deep native-functions-list "target.exe" --project D:\work\proj-x
& $ca --deep native-function-study 0x140001008 --project D:\work\proj-x
& $ca --deep core-evidence-export --path "D:\work\bundle.json" --overwrite true --project D:\work\proj-x
```

- `--project <dir>` 让证据/记忆/journal 落在该目录，**同一目标的多条命令必须共用同一个 project**，否则证据不连续。
- `--format text` 更易读；默认 JSON。`--output-format` 与工具自身的 `--format` 不是一回事。

### 2) MCP（agent 直接调）

已注册。MCP 默认只暴露 **30 个核心工具**（控制上下文），需要更多时调用 `enable_toolset` / `list_toolsets` 按组开启：

| toolset | 数量 | 内容 |
|---|---|---|
| native | 55 | 反汇编、函数、xref、调用图、伪 C、字节、注解 |
| formats | 36 | 目标无关格式：Go/WASM/EVM/PE 资源/ELF/固件/pcap/SQLite |
| core | 36 | 目标开关、证据、unknowns、报告、卷宗 |
| adb | 24 | 连接手机 |
| browser | 22 | CDP 网页 |
| application | 17 | 应用图、比对、重建台账 |
| runtime | 14 / apple 14 / android 14 | 进程捕获 / Apple / APK |
| javascript | 11 / frida 9 / managed 8 / build 6 / java 6 / database 1 | |

### 3) 证据桥 → reverse-skill case（与现有逆向流程合流）

```powershell
& "<REPO>/scripts\craft-anything-evidence-bridge.ps1" `
    -CaseRoot "work\<case>" `
    -BundleJson "D:\work\bundle.json" `
    -CliPath "craft-anything-cli" `
    -LinkedWorkitem WI-002
```

- 写 `evidence/E-*.md` 走 reverse-skill 官方 `append-evidence.ps1`（继承不可变写入与 INDEX.md 维护）；
- **幂等**：Craft Anything 的 evidence id 是记录内容的 SHA-256，已写入过的记录自动跳过，可反复跑；
- 自动维护 `workitems.md` 的 evidence 列与 `timeline.md`；
- 写完按提示跑 `review_case.py --verify-hashes --strict` 自检。

## 净室（默认）与深度

- **净室（clean_room=true，默认）**：不读目标内部，只采外部可见面。所有往内看的工具被**架构级拒绝**（`core_target_open` 等会直接报 `feature_missing: clean_room`）。适合法务敏感场景。
- **深度（`--deep`）**：读代码/文件/运行时。日常逆向都要加 `--deep`。
- 桥脚本会把 `clean_room` 语义写进 notes：净室证据只能支撑「表面长什么样/行为是什么」，不能支撑「内部怎么实现」。**不要拿净室证据下实现结论。**

## 本机实测状态（2026-11-08，v0.5.0）

`doctor`：**healthy: true**；MCP 自检通过（protocol 2025-06-18，30 工具）。**18 项引擎中 16 项就绪。**

引擎全部集中在 `<RE_TOOLS>`，并在 `craft-anything` 的 `engines.*` 设置里逐个登记（不改系统 PATH）：

| 引擎 | 版本 | 路径 | 实测 |
|---|---|---|---|
| Ghidra | 12.2 | `<RE_TOOLS>/ghidra/ghidra_12.2_PUBLIC` | analyzeHeadless 分析 notepad.exe 成功（168 s） |
| Ghidra 的 Java | JDK 25.0.5 | `<JDK25_HOME>` | `ghidra-java` 校验绿（**Ghidra 12.x 要 JDK 25，不是 21**） |
| JADX | 1.5.6 | `<RE_TOOLS>/bin/jadx.bat`（硬编码 JDK 21 的包装） | 反编译真实 APK 类 → `engine=jadx, status=ok` |
| Apktool | 3.0.3 | `<RE_TOOLS>/bin/apktool.bat` | 读出 7507 个资源值 / 85 语言 |
| Vineflower | 1.11.1 | `<RE_TOOLS>/bin/vineflower.bat` | 反编译 apktool.jar → 1002 个源文件 |
| ilspycmd | 9.1.0 | `~\.dotnet\tools\ilspycmd.exe` | 2.1 s 生成 `.decompiled.cs` |
| Frida | 17.26.1 | `C:\Python313\Scripts\frida.exe` | 枚举 112 个进程 |
| GDB | 18.1 | `<RE_TOOLS>/w64devkit/bin/gdb.exe` | 读出 PE 入口点 |
| objdump | 2.47 | 同上目录 | `section_headers` 0.4 s |
| rizin | 0.9.1 | `<RE_TOOLS>/rizin/bin/rizin.exe` | `iI` 返回 37 行 |
| tshark | 4.6.9 | `C:\Program Files\Wireshark\tshark.exe` | 解析 pcap（直接与经 Craft Anything 均通） |
| mitmdump | 12.3.1 | `C:\Python313\Scripts\mitmdump.exe` | 版本与启动正常 |
| rtl_433 | 25.12 | `<RE_TOOLS>/rtl_433/rtl_433.exe` | 可运行（缺真实 I/Q 样本） |
| 另：adb / Chromium / Java 21 | — | `C:\adb\adb.exe`、Chrome、`<RE_TOOLS>\jdk\jdk-21.0.13+9` | 就绪 |

**仍缺（环境限制，非漏装）**：

- **binwalk** —— PyPI 上 binwalk 对 Python 3.13 只有 2.1.0 废弃占位包；2.3.4 需 C 扩展编译；v3 是 Rust，本机无 cargo。
- **unblob** —— 依赖 `pyperscan`，无 Windows wheel，需 MSVC C++ 编译工具链（约 2–6 GB）。要装的话先装 VS Build Tools 再 `pip install unblob`。
- **lldb** —— Apple 调试器，Windows 上无实际用途。
- 这三项在 Craft Anything 里都标注为 *optional second opinion*，主流程不依赖它们。

**rebuild 可用（已实测）**：**不需要 Claude Code**。`ai add` 支持 5 种后端 kind：`anthropic` / `openai` / `openrouter` / `openai_compatible` / `agent_cli`。本机用 **DeepSeek**（`openai_compatible`）跑通了完整 study→build→check→refine，2 轮 check 收敛、状态 `ready`，259 秒。

```powershell
# 一行配好（key 从环境变量读，不落盘）
craft-anything-cli ai add DeepSeek --kind openai_compatible --base-url https://api.deepseek.com --key-env DEEPSEEK_API_KEY --model deepseek-chat
craft-anything-cli ai test DeepSeek          # 验连通
craft-anything-cli ai builder DeepSeek       # 指定由它来写代码
```

坑（都踩过）：
- `ai detect` **只检测环境变量是否存在，不验证有效性**。本机 `OPENROUTER_API_KEY` 存在但 401 失效。
- `DEEPSEEK_API_KEY` 只在 **User 级**，当前会话进程可能没继承 → `ai add` 报 `environment variable ... is not set`。解决：`$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')`。
- 用量要自己看 `usage.json`：实测一次小 rebuild 消耗 **326 万 input tokens / 2.4 万 output**，但 `cost_usd` 显示 0.0 —— **DeepSeek 不在它的价格表里（`priced: false`），不等于没花钱**。

**Reasonix 不能当这个编码 agent**：它只有 Electron GUI（`Reasonix.exe`，无 CLI/headless 入口），而 rebuild 靠**子进程**驱动 agent。内置 adapter 是编译期注册的（源码 `AgentAdapters`，"any a fork registers"），运行时加不了。想让 Reasonix 接管，得给 Craft Anything 写一个 fork 的 adapter。

**实测样例**：`notepad.exe` triage 识别 pe/x86_64/stripped/入口 `0x140023bd0`；`native-functions-list` 发现 **428 个函数**；用户自己的 `BanLu2.apk` 解析出 `com.voicenote.app` v2.0.0、10 个 DEX、17,997 类、**签名 cryptographically VERIFIED**。

## 与现有逆向流程的分工

```
发现    reverse-index（17,704 条已逆向项目 + 131 条「被杀死又复活」记录）
        search（中文自动转英文）→ info（README）→ clone（浅克隆到 vendor/ 真读源码）
  ↓
流程    reverse-skill（work/<case>：scope / workitems / timeline / 不可变 evidence / case-review 校验）
  ↓
执行    ├─ Craft Anything   自带解析器，静态结构 + 净室观察 + 重建（首选入口）
        ├─ REA              Electron/JS 语义图、IPC、大工程 AST（BYO 引擎）
        └─ Ghidra / jadx / Frida / rizin / gdb / objdump / tshark  ← 本机已全部就绪
  ↓
证据    REA  ──rea-evidence-bridge.ps1──────────┐
        Craft ──craft-anything-evidence-bridge.ps1──┴→ evidence/E-*.md → case-review
```

选型口诀：**先 reverse-index 查重（死产品先查 resurrected）→ 找到就 clone 下来读 → 静态结构用 Craft Anything 自带解析器 → 需要类型恢复才上 Ghidra → JS/Electron 语义用 REA → 动态看 Frida/GDB**。

## 规则

- 只分析你拥有或获准分析的目标；Craft Anything 不绕过 DRM/登录/反作弊，本技能也不用于此。
- 深度模式会读原程序代码，注意合规边界；对外交付时标明证据来自净室还是深度。
- 它的伪 C 无类型恢复，`asm_fallbacks`/`goto`/`structured` 字段要一起读，别把伪 C 当源码。
- 大结果先看摘要再分页（`offset`/`limit`），不要把整份 evidence 灌进上下文。
- 二进制来源：只用官方 release（`storytold/craft-anything`），并核对 `SHA256SUMS.txt`。

## 故障排查

- **下载慢/连不上**：`release-assets.githubusercontent.com` 在本机会被解析成 `127.0.0.1`（DNS 污染）。用 `curl --resolve release-assets.githubusercontent.com:443:185.199.110.133` 直连 CDN，或 `ghproxy.net` 前缀（限速约 10 KB/s，不推荐大文件）。
- **路径含空格**：MCP 配置按空格切分参数，必须把 exe 放到无空格路径（例如 `<CRAFT_ANYTHING_HOME>/bin/`）。
- **净室报 `feature_missing`**：加 `--deep`，或 `settings_set deep_mode true`。
- **证据不连续**：确认每条命令带同一个 `--project`。
