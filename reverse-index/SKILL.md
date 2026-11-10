---
name: reverse-index
description: 在逆向工程动手前检索 17,704 个「已经被逆向过」的开源项目索引（非官方客户端、驱动、协议解析、固件工具、反编译/重实现）。支持中文查询自动转英文、Windows/Android/协议/固件子集过滤；`clone` 可把候选项目浅克隆到 vendor/ 直接读源码；`resurrected` 查 129 个被厂商杀死又被开源复活的产品。与 Craft Anything / REA / reverse-skill 协同。用于「有没有人做过」「找现成实现」「找协议/格式/固件工具」「这产品死了还有救吗」。
---

# reverse-index — 逆向索引检索适配器

本地适配器：**awesome-reverse-engineered**（17,728 条，CC0 数据）→ 本地子集 + 中英检索 + 自动同步。

## 何时使用

- 准备逆向一个封闭 App / API / 设备 / 协议 / 文件格式 / 游戏之前；
- 需要找现成的非官方客户端、SDK、驱动、固件工具、协议解析、反编译项目；
- 用户用中文描述目标（“微信协议”“小米摄像头”“路由器固件”“安卓脱壳”）。

## 何时不要用

- 找 exploit / 破解 / DRM 绕过 / 外挂 / 验证码破解（索引主动排除，且本技能不提供）；
- 替代 REA、Ghidra、jadx、Frida 等真正的分析工具；
- 作为许可证或合规结论来源（约 29% 条目没有明确 license）。

## 命令

脚本绝对路径：

```
<REPO>/reverse-index/re_index.py
```

```powershell
# 中文查询（自动转英文）
python "D:\...\reverse-index\scripts\re_index.py" search "微信 协议" --facet protocol

# 找 Windows 驱动 / 注册表工具
python "...\re_index.py" search "windows driver" --facet windows --min-stars 100

# 安卓固件提取
python "...\re_index.py" translate "安卓 固件 提取"
python "...\re_index.py" search "安卓 固件 提取" --facet android --facet firmware

# 只看本地子集，按 star 排序
python "...\re_index.py" search "路由器 抓包" --subset-only --sort stars --limit 20

# 条目详情（可选拉 README）
python "...\re_index.py" info make-all/tuya-local --lines 60

# 拉到本地读源码（只克隆索引里收录的项目；浅克隆，不编译不运行）
python "...\re_index.py" clone codetheweb/tuyapi
# -> D:\...\reverse-index\vendor\codetheweb__tuyapi

# 被厂商杀死、又被开源复活的产品（129 条）
python "...\re_index.py" resurrected Nintendo
python "...\re_index.py" resurrected --kind service --limit 30

# 查看同步状态
python "...\re_index.py" doctor
```

`--json` 输出机器可读结果；`--explain` 显示中文→英文映射；`--no-translate` 保留原始查询。
`info` / `clone` / `resurrected` 都接受 `owner/repo` 简写、GitHub URL 或纯项目名。

## 工作流

1. **翻译**：中文先 `translate`，确认英文检索词；词典在 `data/aliases.json`，可自行补充。
2. **检索**：优先 `--facet` + `--min-stars 50`；排序默认 relevance（已修正“小仓库名称精确匹配压过大项目”的问题）。
3. **选择**：按以下顺序判断：
   - 是否真的做这件事（本地控制 vs 云端、读 vs 写）；
   - `pushed` 是否在 1–2 年内，是否 archived；
   - license 是否可用；空白 = 未声明 = 默认保留所有权利；
   - 语言/包管理器是否匹配；
   - 同等条件选 star 更高的。
4. **读 README**：`info owner/repo --lines 80`，确认安装、认证、限制。
5. **要动代码就拉下来**：`clone owner/repo` 浅克隆到 `vendor/`，交给 Craft Anything 或直接读源码——比只看网页 README 判断得准。
6. **告知用户**：选了什么、为什么、license、风险（非官方客户端可能违反 ToS）。
7. **没有现成的**：才进入自研/逆向；做完考虑回馈上游索引（见上游 CONTRIBUTING）。

## 与执行层的协同（Craft Anything / REA / reverse-skill）

本技能是 **discovery 层**，只回答「有没有人做过」。真正动手是执行层：

| 层 | 工具 | 何时 |
|---|---|---|
| 发现 | **本技能** | 动手前一律先查；死产品先查 `resurrected` |
| 静态结构 | **Craft Anything**（自带解析器，已装 Ghidra/jadx/rizin 等） | 读二进制/APK/Electron/游戏文件 |
| 语义深挖 | REA（Electron/JS 语义图、IPC） | JS 应用要看模块与通道 |
| 流程与证据 | reverse-skill（`work/<case>` + 不可变 evidence + case-review） | 需要可审计的结论链 |
| 动态 | Frida / GDB（本机已装） | 要看运行时行为 |

链路：

```powershell
# 1 找
python "...\re_index.py" search "安卓 固件 提取" --facet android
# 2 拉到本地
python "...\re_index.py" clone <owner/repo>
# 3 用执行层读它，而不是只看网页
craft-anything-cli --deep core-target-open --path "...\vendor\<owner>__<repo>"
# 4 证据入 case（Craft Anything 的 bundle → reverse-skill）
.\craft-anything-evidence-bridge.ps1 -CaseRoot work\<case> -BundleJson <bundle.json>
```

导出行（REA/agent 可直接消费）：

```powershell
python "...\re_index.py" export-rea --facet windows
```

MCP：`python "...\re_index.py" mcp` 暴露 6 个只读工具（含 `search_resurrected_products`）。
官方另提供 `npx skills add alexgaoth/awesome-reverse-engineered`（skill）、Claude Code plugin、
以及 `uvx --from "git+...#subdirectory=mcp" reverse-engineered-mcp`（官方 MCP）；本机用本地适配器，
因为它多了中文翻译、facet 子集、`clone`、`resurrected` 与 REA 导出。

## 数据来源与许可

- 上游：**alexgaoth/awesome-reverse-engineered**（17,704 条，每晚自动刷新）
- 许可：**代码 MIT + 数据 CC0**（见上游 `LICENSE` / `LICENSE-DATA`）——数据可自由使用
- 本地数据文件：
  - `data/entries.json` — 主索引（17,704 条）
  - `data/resurrected.json` — 129 条「被厂商杀死、被开源复活」记录
  - `data/removed.json` — 106 条已下架记录（仅存档）
- 上游另有：搜索站、`categories/*.md`（按分类的说明）、`bench/`（Interop-Bench：测 agent 能否自主逆向出协议/格式）、`SOURCES.md`（数据出处）
- ⚠️ 约 29% 条目**没有明确 license**：空白 = 未声明 = 默认保留所有权利，用前必须核对。

## 自动同步

安装时会注册 Reasonix `SessionStart` hook：

```
"<python>" "D:\...\re_index.py" sync --hook --max-age 3600 --timeout 45 --retries 1 --detach
```

- 启动 Reasonix 时自动检查 GitHub 上的 `entries.json`；
- `--detach` 在后台进程执行，不阻塞会话；
- 1 小时内只检查一次；304/内容未变则跳过；
- 优先用系统 `curl`，并依次尝试 raw.githubusercontent.com / jsDelivr / github.com-raw；
- 下载失败保留旧数据，不阻塞会话；
- 只拉取，不向 GitHub 推送。

手动同步：

```powershell
python "...\re_index.py" sync --force
```

## 规则

- 先确认是否有官方 API/SDK；有就用官方的。
- 只用于用户自己的账号、设备和数据；告知 ToS/封号风险。
- 不用于批量抓取、垃圾信息、绕过付费墙/DRM/安全控制。
- 下载的代码视为不可信；运行前阅读，不要把凭据交给未读过的工具。
- 索引可能过期；`info` / clone 失败就换下一个结果。

## 故障排查

- 中文 Windows 控制台出现 `UnicodeDecodeError: 'gbk'`：本适配器已全程显式 UTF-8；若调用上游脚本仍遇到，设 `PYTHONUTF8=1`。
- `index data missing`：先运行 `sync --force`。
- 网络慢/超时：`sync --source <本地 entries.json>`。
- 查看日志：`sync.log`；查看状态：`doctor`。
