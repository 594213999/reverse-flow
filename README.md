# reverse-flow

**一套可组装的逆向工程工作流**：从「有没有人做过」到「看懂它」，再到「照着重做一个」，每一步都有证据可查。

这个仓库不是又一个逆向工具，而是**把现有的开源工具按分层职责接起来**的粘合层 + 我写的两个证据桥。核心思路是让「发现 → 执行 → 证据 → 重建」四层各司其职，任何一层都能替换。

> 本仓库的原创部分（两个证据桥、`reverse-index` 适配器、文档）采用 MIT。
> 第三方工具的许可见 [THIRD-PARTY.md](THIRD-PARTY.md)，**它们的许可是你自己的使用条件**。

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [四层架构](#四层架构)
- [各模块下载来源](#各模块下载来源)
- [快速开始](#快速开始)
- [证据桥](#证据桥)
- [许可与归属](#许可与归属)
- [实测记录](#实测记录)

---

## 它解决什么问题

逆向一个封闭系统时，真实的浪费发生在三处：

| 浪费 | 表现 | 本流程的解法 |
|---|---|---|
| **重复劳动** | 花两周逆向的东西，别人三年前就开源了，只是没人知道 | 第 0 层：先查 17,704 条「已被逆向」的索引 |
| **结论不可查** | 三个月后没人记得「这个函数是干嘛的」是怎么得出来的 | 第 3 层：每条结论落成不可变证据，带置信度、局限和覆盖度 |
| **工具链断裂** | 看懂了，但「照着重做一个」还是手工活 | 第 4 层：study → build → check → refine 闭环 |

---

## 四层架构

```
┌─ 0. 发现 ──────────────────────────────────────────────────────┐
│  reverse-index  →  awesome-reverse-engineered（17,704 条）      │
│  中文查询离线转英文 · facet 子集 · clone 拉源码 · resurrected   │
└────────────────────────────┬───────────────────────────────────┘
                             ▼
┌─ 1. 执行（自带引擎，静态为主）─────────────────────────────────┐
│  Craft Anything: PE/ELF/Mach-O/DEX/APK/ASAR/plist/.NET/Go/      │
│  Unity/Unreal/SQLite/pcap 解析器 + 伪 C，无需外部程序          │
└────────────────────────────┬───────────────────────────────────┘
                             ▼
┌─ 2. 执行（深度，可选）─────────────────────────────────────────┐
│  Ghidra 12.x · JADX · Apktool · Frida · GDB · objdump ·        │
│  rizin · tshark · mitmproxy · Vineflower · ILSpy               │
└────────────────────────────┬───────────────────────────────────┘
                             ▼
┌─ 3. 证据 ──────────────────────────────────────────────────────┐
│  reverse-skill 的 work/<case>/  ←  两个证据桥写入              │
│  REA / Craft Anything 的结论  →  E-*.md（不可变，内容寻址）     │
└────────────────────────────┬───────────────────────────────────┘
                             ▼
┌─ 4. 重建（可选）───────────────────────────────────────────────┐
│  Craft Anything rebuild: study → build → check → refine        │
│  需要任意一个 AI 后端（API key 即可，不必装 Claude Code）      │
└────────────────────────────────────────────────────────────────┘
```

**分层是可替换的。** 第 1 层嫌工具重，直接用第 2 层的 Ghidra；第 2 层没装，第 1 层的自带解析器也能出结果；第 3、4 层完全可选。

---

## 各模块下载来源

**所有第三方组件的官方来源、版本和校验方式如下。** 建议逐一核对，不要相信任何二手的镜像。

### 0. 发现层

| 模块 | 来源 | 许可 | 说明 |
|---|---|---|---|
| **awesome-reverse-engineered** | `https://github.com/alexgaoth/awesome-reverse-engineered` | **代码 MIT + 数据 CC0-1.0** | 数据在 `data/entries.json`，每晚由 CI 刷新 |
| `reverse-index`（本仓库） | 本仓库 `reverse-index/` | MIT | 上游数据的本地适配器，见下 |

`reverse-index` 是**本仓库自带的适配器**，不是上游官方脚本。相对上游（`mcp/re_index.py`，179 行）多了：中文查询离线翻译、`--facet` 子集、`sync` 自动同步、`clone` 拉源码、`resurrected` 死产品复活查询、`export-rea`。

数据来源（拉取，不推送）：

- `https://raw.githubusercontent.com/alexgaoth/awesome-reverse-engineered/main/data/entries.json`
- 备选：`https://cdn.jsdelivr.net/gh/alexgaoth/awesome-reverse-engineered@main/data/entries.json`
- `data/resurrected.json`（131 条「被厂商杀死、被开源复活」记录）

> 上游也提供官方 skill（`npx skills add alexgaoth/awesome-reverse-engineered`）、Claude Code plugin 和官方 MCP（`uvx --from "git+https://github.com/alexgaoth/awesome-reverse-engineered#subdirectory=mcp" reverse-engineered-mcp`）。用哪个都行，本仓库用自带的因为多了中文检索和 `clone`。

### 1. 执行层（自带引擎）

| 模块 | 来源 | 许可 | 说明 |
|---|---|---|---|
| **Craft Anything** | `https://github.com/storytold/craft-anything/releases` | MIT / Apache-2.0 双许可 | 纯 Rust。Windows/macOS/Linux/FreeBSD 安装包 + 便携版，每版附 `SHA256SUMS.txt` |

**下载后必须核对校验和**：

```bash
# 便携版（推荐，不写注册表）
curl -L -O https://github.com/storytold/craft-anything/releases/download/<ver>/craft-anything-<ver>-windows-x64-portable.zip
curl -L -O https://github.com/storytold/craft-anything/releases/download/<ver>/SHA256SUMS.txt
sha256sum -c SHA256SUMS.txt --ignore-missing
```

> ⚠️ **实测坑**：GitHub API 报的 `size` 可能比真实文件大几个字节（本项目实测差 6157 字节），按 size 分段下载会永远「差一点」。以 `content-length` 为准。
>
> ⚠️ 若 `release-assets.githubusercontent.com` 下载失败，先查 DNS 是否被解析到 `127.0.0.1`（部分加速器/代理会污染）。可临时 `curl --resolve release-assets.githubusercontent.com:443:<真实IP>`。

### 2. 执行层（深度，全部可选）

Craft Anything 通过 `settings-set` 把这些引擎的**可执行文件路径**登记进去即可生效，不需要改系统 `PATH`：

```bash
craft-anything-cli settings-set --key engines.tools.<id> --value '"<绝对路径>"'
craft-anything-cli settings-set --key engines.ghidra.install_dir --value '"<ghidra 目录>"'
craft-anything-cli settings-set --key engines.ghidra.java_home   --value '"<JDK 目录>"'
craft-anything-cli engines-list        # 看哪些已就绪
craft-anything-cli doctor              # 全量自检
```

| 引擎 | 设置键 | 官方来源 | 许可 | 版本要求 |
|---|---|---|---|---|
| **Ghidra** | `engines.ghidra.install_dir` | `https://github.com/NationalSecurityAgency/ghidra/releases` | Apache-2.0 | **需要 JDK 25+**（12.x 起，21 不行） |
| **JDK（Ghidra 用）** | `engines.ghidra.java_home` | `https://adoptium.net/`（国内镜像：`https://mirrors.tuna.tsinghua.edu.cn/Adoptium/25/jdk/x64/windows/`） | GPL-2.0 + CE | 25.0.5 实测可用 |
| **JDK（jadx/apktool 用）** | `engines.tools.java` | 同上，21 亦可 | 同上 | 17+ |
| **JADX** | `engines.tools.jadx` | `https://github.com/skylot/jadx/releases` | Apache-2.0 | 1.5.6 实测 |
| **Apktool** | `engines.tools.apktool` | `https://github.com/iBotPeaches/Apktool/releases` | Apache-2.0 | 3.0.3 实测（`.jar`，需包一层启动脚本） |
| **Vineflower** | `engines.tools.vineflower` | `https://repo1.maven.org/maven2/org/vineflower/vineflower/` | Apache-2.0 | 1.11.1 实测 |
| **ILSpy (ilspycmd)** | `engines.tools.ilspy` | `dotnet tool install --global ilspycmd --version 9.1.0.7988` | MIT | 需 .NET SDK；**10+ 要更高 SDK** |
| **Frida** | `engines.tools.frida` | `pip install frida-tools` | wxWindows Library Licence | 17.26.1 实测 |
| **GDB + objdump** | `engines.tools.gdb` / `.objdump` | `https://github.com/skeeto/w64devkit/releases` | GPL-3.0 | 一次装好两件（GDB 18.1 / binutils 2.47） |
| **rizin** | `engines.tools.rizin` | `https://github.com/rizinorg/rizin/releases` | LGPL-3.0 | 0.9.1 实测 |
| **tshark** | `engines.tools.tshark` | `https://www.wireshark.org/download.html`（或 `winget install WiresharkFoundation.Wireshark`） | GPL-2.0 | 4.6.9 实测 |
| **mitmproxy** | `engines.tools.mitmdump` | `pip install mitmproxy` | MIT | 12.3.1 实测 |
| **rtl_433** | `engines.tools.rtl433` | `https://github.com/merbanan/rtl_433/releases`（选 `win-x64`） | GPL-2.0 | 25.12 实测 |
| **adb** | `engines.tools.adb` | `https://developer.android.com/tools/releases/platform-tools` | Apache-2.0 | — |

**已知装不上的（Python 3.13 + 无 MSVC 工具链）**：

- `binwalk` —— PyPI 对 3.13 只有 2.1.0 废弃占位包；2.3.4 需编译 C 扩展；v3 是 Rust。要装：先装 rustup 再 `cargo install binwalk`。
- `unblob` —— 依赖 `pyperscan`，无 Windows wheel，需 VS Build Tools（2–6 GB）后 `pip install unblob`。
- `lldb` —— Apple 调试器，Windows 上无实际用途。

这三个在 Craft Anything 里都标 `optional second opinion`，主流程不依赖。

### 3. 证据层

| 模块 | 来源 | 许可 | 说明 |
|---|---|---|---|
| **reverse-skill** | 第三方技能包（`skills/reverse-engineering`、`skills/case-review` 等） | 见其自身 LICENSE | 提供 `work/<case>/` 结构、`append-evidence.ps1`、`review_case.py` |
| `craft-anything-evidence-bridge.ps1` | **本仓库** | MIT | Craft Anything bundle → `E-*.md` |
| `rea-evidence-bridge.ps1` | **本仓库** | MIT | REA JSON → `E-*.md` |

两个桥都**不自己写证据文件**，一律委托 `append-evidence.ps1`，因此自动继承它的不可变写入、字段校验和 `INDEX.md` 维护。

### 4. 重建层

| 模块 | 来源 | 许可 | 说明 |
|---|---|---|---|
| **Craft Anything rebuild** | 同上（Craft Anything 内置） | MIT / Apache-2.0 | 需要**任意一个** AI 后端 |
| AI 后端（可选） | 任何 OpenAI 兼容 API：DeepSeek / OpenAI / OpenRouter / Anthropic / 本地 llm | 各自条款 | **不必装 Claude Code**，见下 |

---

## 快速开始

### 1. 装 Craft Anything

```bash
# 从 releases 下载，核对 SHA256SUMS.txt 后解压
# 放到无空格路径（MCP 按空格切分参数）
<CRAFT_ANYTHING_HOME>/craft-anything-cli.exe --version
<CRAFT_ANYTHING_HOME>/craft-anything-cli.exe doctor
```

### 2. 接 MCP（让它成为你的 agent 的眼睛）

任意 MCP 客户端：

```json
{
  "mcpServers": {
    "craft-anything": {
      "command": "<CRAFT_ANYTHING_HOME>/craft-anything-cli.exe",
      "args": ["mcp"]
    }
  }
}
```

默认暴露 30 个核心工具；需要更多时调 `enable_toolset` 按组开启（native 55 / formats 36 / core 36 / android 14 / browser 22 …）。

### 3. 配 AI 后端（只有第 4 层需要）

```bash
craft-anything-cli ai add DeepSeek --kind openai_compatible \
  --base-url https://api.deepseek.com --key-env DEEPSEEK_API_KEY --model deepseek-chat
craft-anything-cli ai test DeepSeek          # 验连通
craft-anything-cli ai builder DeepSeek       # 指定由它来写代码
```

`ai add --kind` 支持 `anthropic` / `openai` / `openrouter` / `openai_compatible` / `agent_cli`。**只有 `agent_cli` 才需要 Claude Code 这类已装 CLI；前四种是纯 API。**

### 4. 先查有没有人做过

```bash
python reverse-index/re_index.py search "安卓 固件 提取" --facet android
python reverse-index/re_index.py info owner/repo --lines 60
python reverse-index/re_index.py clone owner/repo        # 浅克隆到 vendor/ 读源码
python reverse-index/re_index.py resurrected Nintendo    # 死产品复活
```

### 5. 分析

```bash
craft-anything-cli --deep triage target.bin --project <WORK>/proj
craft-anything-cli --deep native-functions-list target.bin --project <WORK>/proj
craft-anything-cli --deep core-evidence-export --path <WORK>/bundle.json --overwrite true
```

> `--deep` 是必需的：默认**净室模式**会拒绝所有「往目标内部看」的工具（架构级白名单，见 Craft Anything 的 `clean_room.rs`）。

### 6. 证据入 case

```powershell
./scripts/craft-anything-evidence-bridge.ps1 -CaseRoot work/<case> -BundleJson <WORK>/bundle.json
```

---

## 证据桥

### 为什么需要桥

Craft Anything 和 REA 都产出高质量证据，但格式不同；`reverse-skill` 要求固定的 `E-*.md` 结构。桥只做**格式适配**，不做二次判断。

### 共用设计

- **委托写入**：一律调官方 `append-evidence.ps1`（不可变 + `INDEX.md` + 哈希校验）
- **幂等**：两边的证据 id 都是内容 SHA-256，已写入过的自动跳过
- **不吞信息**：`limitations`、`coverage`、`clean_room` 语义全部保留入证
- **可用 `-DryRun` 预览**，不落地

### craft-anything-evidence-bridge

```powershell
./scripts/craft-anything-evidence-bridge.ps1 `
  -CaseRoot work/my-case `
  -BundleJson <WORK>/bundle.json `
  -CliPath <CRAFT_ANYTHING_HOME>/craft-anything-cli.exe `
  -LinkedWorkitem WI-002
```

- 一个 bundle 内含**多条**记录，桥循环写入（`-MaxRecords` 保护）
- **幂等**：重复导出自动跳过
- **净室语义入证**：`clean_room=true` 的记录会标注「只支撑表面行为，不支撑内部实现」
- 自动维护 `workitems.md` 的 evidence 列和 `timeline.md`

### rea-evidence-bridge

```powershell
./scripts/rea-evidence-bridge.ps1 -CaseRoot work/my-case -ReaJson rea-out.json
```

- REA 一次输出**一条** Evidence（不加 `--filter-output` 时实测可达 212 MB 单行）
- `-MaxJsonChars` 默认 16 MiB，超限直接拒绝并提示用 `--filter-output`

> 两个桥都是 `.ps1` 且**含中文**，必须存为 **UTF-8 with BOM**（PowerShell 5.1 按 GBK 读无 BOM 文件会语法错乱）。

---

## 许可与归属

| 对象 | 许可 |
|---|---|
| 本仓库的原创代码与文档 | **MIT**（见 [LICENSE](LICENSE)） |
| awesome-reverse-engineered 的**数据** | CC0-1.0（可自由使用） |
| awesome-reverse-engineered 的代码 | MIT |
| Craft Anything | MIT / Apache-2.0 双许可 |
| Ghidra | Apache-2.0 |
| JADX / Apktool / Vineflower | Apache-2.0 |
| reverse-skill | 见其自身 LICENSE |
| 其余引擎 | 见 [THIRD-PARTY.md](THIRD-PARTY.md) |

**本仓库不包含任何第三方二进制。** 全部由你从各自官方渠道下载。

### 使用边界

- 只分析你**拥有或获准分析**的软件。
- 本流程不绕过 DRM、登录或反作弊。
- 「重建」是依据证据复现布局与行为；**不要复制 logo、照片、商标或文本**，除非你拥有它们或已获授权。
- 数据索引里约 29% 条目**没有声明 license**。空白 = 未声明 = 默认保留所有权利，用前必须核对。
- 深度模式会读取目标内部（代码/文件/运行时），对外的结论要标明证据来自净室还是深度。

---

## 实测记录

这些都是**本仓库作者在自己机器上真跑过的**，不是文档推导。数据截至 2026-11。

### 环境

- Windows 10.0.19045 x64、Python 3.13.13、.NET SDK 9.0.308、JDK 21 + 25

### Craft Anything 引擎就绪度：18 项中 16 项

| 引擎 | 版本 | 实测结论 |
|---|---|---|
| Ghidra | 12.2 | `analyzeHeadless` 分析 `notepad.exe` 成功（168 s） |
| Ghidra 的 Java | JDK 25.0.5 | 12.x **要 25**，给 21 会直接拒绝 |
| JADX | 1.5.6 | 反编译真实 APK 的 `MainActivity` → `engine=jadx, status=ok` |
| Apktool | 3.0.3 | 读出 7,507 个资源值 / 85 种语言 |
| Vineflower | 1.11.1 | 反编译 `apktool.jar` → 1,002 个源文件 |
| ILSpy | 9.1.0 | 2.1 s 生成 `.decompiled.cs` |
| Frida | 17.26.1 | 枚举 112 个进程 |
| GDB / objdump | 18.1 / 2.47 | 读出 PE 入口点 / `section_headers` 0.4 s |
| rizin | 0.9.1 | `iI` 返回 37 行 |
| tshark | 4.6.9 | 解析 pcap（直接与经 Craft Anything 均通） |
| mitmproxy | 12.3.1 | 版本与启动正常 |
| rtl_433 | 25.12 | 可运行（缺真实 I/Q 样本） |

### 自带解析器（无需任何外部引擎）

- `notepad.exe` → `pe / x86_64 / stripped / 入口 0x140023bd0`，**428 个函数**（`function_starts`，derived 置信度）
- 真实 APK（102 MB）→ 包名、10 个 DEX、17,997 类、**签名 cryptographically VERIFIED**

### rebuild 闭环：可用，不需要 Claude Code

用 DeepSeek（`openai_compatible`）跑完整流程：**259 秒**，check 1 失败 → 改进 → check 2 全过 → `status: ready`。生成物真跑 `python greet.py` → 打印并 **exit 0**。

> 成本提醒：一次小 rebuild 消耗 **326 万 input tokens**，但 `usage.json` 的 `cost_usd` 显示 0.0 —— 因为 DeepSeek 不在它的价格表（`priced: false`）。**这不是免费。**

### 踩过的坑（按痛苦程度排序）

1. **Ghidra 12.x 要 JDK 25**，不是 21。给 21 会报 `JDK 25+ (64-bit) could not be found`。
2. **360 安全卫士拦截 `api-ms-win-*.dll` 的创建**，JDK 解压反复 `Access denied`，且只针对这几个**特定文件名**（改名能建）。用 7-Zip 解压可绕过。
3. **`ai detect` 只检测环境变量是否存在，不验证有效性**。环境里有 key 照样可能 401。
4. **`JAVA_HOME` 被污染导致 jadx 静默 exit 1**，stderr 为空。解法：给 jadx 包一层硬编码 JDK 路径的启动脚本，不依赖环境变量。
5. **GitHub release 的 `size` 与真实文件不一致**，按 size 分段会永远差几个字节。
6. **Craft Anything 不认 RISC-V**：`--architecture` 只接受 `x86/x86_64/arm/arm64`；`--language-id RISCV:LE:32:default` 不能单独生效；用 `arm` 占位能跑但走的是自研引擎，不是 Ghidra 桥。**非 x86/ARM 目标直接用 Ghidra。**
7. **Ghidra 的自动分析在无符号裸 RV32 上找到 0 个函数**，需要自己扫 opcode 合法段再分段反汇编。

---

## 可选的校准器

上游自带 **Interop-Bench**（`bench/`），用随机生成的协议/文件格式测「能否写出能工作的互操作代码」，**代码跑分、无 LLM 评判**，纯标准库。

```bash
python bench/run.py one --agent reference --track proto --level 5   # 校 oracle
python bench/run.py matrix --agents 'cmd:your-agent {prompt}' --seeds 3 --out mine.jsonl
```

分级即逆向难度标尺：L1 定长记录 → L3 类 Garmin FIT / 小米 miIO → **L5 校验只以 RISC-V 机器码存在于固件里**。

---

## 贡献

欢迎 PR。原则：**每层可替换、证据优先、不吞掉局限性**。

新增引擎/工具时，请同时更新 [THIRD-PARTY.md](THIRD-PARTY.md) 里的来源与许可。
