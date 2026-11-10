# reverse-index — 上游索引数据的本地适配器

这是 ['awesome-reverse-engineered'](https://github.com/alexgaoth/awesome-reverse-engineered)
数据的本地适配器，是 [reverse-flow](../README.md) 流程的**第 0 层（发现）**。

上游原始脚本（`mcp/re_index.py`，179 行）只做检索；本适配器在它基础上增加了：

| 能力 | 命令 | 为什么需要 |
|---|---|---|
| 中文查询离线转英文 | `search "安卓 固件 提取"` | 上游只认英文，中文查询会零结果 |
| facet 子集过滤 | `--facet windows/android/protocol/firmware` | 17,704 条里缩小到你要的那类 |
| 自动同步 | `sync --force` / SessionStart hook | 上游每晚刷新 |
| **浅克隆候选项目** | `clone owner/repo` | 只看 README 判断不准，读源码才准 |
| 死产品复活查询 | `resurrected Nintendo` | 131 条「被厂商杀死、被开源救活」的记录 |
| MCP server | `mcp` | 6 个只读工具，含 `search_resurrected_products` |
| REA/agent 导出 | `export-rea --facet windows` | JSONL，给下游直接消费 |

## 安装

只需要 Python 3.10+（标准库为主；有系统 `curl` 时会优先用它下载）。

```bash
cd reverse-index
python re_index.py sync --force     # 拉数据并生成 subset/INDEX.md
python re_index.py doctor           # 自检
```

## 用法

```bash
# 检索（中文自动转英文）
python re_index.py search "微信 协议" --facet protocol
python re_index.py search "windows driver" --facet windows --min-stars 100

# 条目详情（读 README）
python re_index.py info codetheweb/tuyapi --lines 60

# 拉到本地读源码（只克隆索引里收录的项目）
python re_index.py clone codetheweb/tuyapi
#   -> vendor/codetheweb__tuyapi

# 死产品复活
python re_index.py resurrected Nintendo
python re_index.py resurrected --kind service --limit 30

# 其它
python re_index.py stats
python re_index.py export-rea --facet android
python re_index.py mcp                  # stdio MCP server
```

`info` / `clone` / `resurrected` 都接受 `owner/repo` 简写、GitHub URL 或纯项目名。

## 数据

| 文件 | 内容 |
|---|---|
| `data/entries.json` | 主索引（17,704 条），CC0-1.0 |
| `data/resurrected.json` | 131 条「被杀死又复活」记录 |
| `data/removed.json` | 106 条已下架记录（仅存档） |
| `data/aliases.json` | 中文→英文检索词典（246 条，可自补） |
| `data/subset.json` | 生成的四个 facet 子集（机器可读） |
| `INDEX.md` | 生成的人类可读索引 |

数据不入 git（见 `.gitignore`），由 `sync` 拉取。

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py"
```

31 项，覆盖翻译、排序、facet、subset、sync、`find_entry` 多种写法、`resurrected` 检索与 MCP 工具、`clone` 的三条拒绝路径。

## 规则

- 上游检索只用于**你拥有或获准分析**的目标。
- 索引本身排除 exploit / 破解 / DRM 绕过 / 外挂类项目。
- 约 29% 条目**没有声明 license**：空白 = 未声明 = 默认保留所有权利，用前必须核对。
- `clone` 只克隆索引里已收录的 GitHub 项目；它是研究捷径，不是通用 cloner。下载的代码视为不可信，运行前先读。
- 本适配器只**拉取**上游数据，不向任何地方推送。
