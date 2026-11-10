# Third-party components

This repository contains **no third-party binaries**. Everything below is downloaded by you
from its official channel. Versions are the ones verified in this project's own testing
(see the "实测记录" section of the README); newer versions likely work.

If you redistribute anything derived from these, **their licences apply, not this repo's MIT**.

---

## Data

| Component | Source | Licence | Notes |
|---|---|---|---|
| awesome-reverse-engineered (data) | https://github.com/alexgaoth/awesome-reverse-engineered | **CC0-1.0** | `data/entries.json`, `data/resurrected.json`; refreshed nightly by upstream CI. CC0 permits any use; attribution appreciated, not required. |
| awesome-reverse-engineered (code) | same | MIT | Upstream scripts, site, bench |

## Execution layer — built-in engine

| Component | Source | Licence | Notes |
|---|---|---|---|
| Craft Anything | https://github.com/storytold/craft-anything/releases | MIT OR Apache-2.0 (dual) | Pure Rust. Verify `SHA256SUMS.txt` from the same release. Brand assets in `docs/brand/` are trademarked and outside the licence. |

## Execution layer — optional deep engines

| Component | Source | Licence | Version verified |
|---|---|---|---|
| Ghidra | https://github.com/NationalSecurityAgency/ghidra/releases | Apache-2.0 | 12.2 (needs **JDK 25+**) |
| Eclipse Temurin JDK | https://adoptium.net/ — mirror: https://mirrors.tuna.tsinghua.edu.cn/Adoptium/ | GPL-2.0 WITH Classpath Exception | 21.0.13, 25.0.5 |
| JADX | https://github.com/skylot/jadx/releases | Apache-2.0 | 1.5.6 |
| Apktool | https://github.com/iBotPeaches/Apktool/releases | Apache-2.0 | 3.0.3 |
| Vineflower | https://repo1.maven.org/maven2/org/vineflower/vineflower/ | Apache-2.0 | 1.11.1 |
| ILSpy (ilspycmd) | `dotnet tool install --global ilspycmd` (NuGet) | MIT | 9.1.0.7988 |
| Frida / frida-tools | `pip install frida-tools` (PyPI) | wxWindows Library Licence | 17.26.1 / 14.13.0 |
| w64devkit (GDB + binutils) | https://github.com/skeeto/w64devkit/releases | GPL-3.0 | GDB 18.1 / binutils 2.47 |
| rizin | https://github.com/rizinorg/rizin/releases | LGPL-3.0 | 0.9.1 |
| Wireshark / tshark | https://www.wireshark.org/download.html · `winget install WiresharkFoundation.Wireshark` | GPL-2.0 | 4.6.9 |
| mitmproxy | `pip install mitmproxy` (PyPI) | MIT | 12.3.1 |
| rtl_433 | https://github.com/merbanan/rtl_433/releases | GPL-2.0 | 25.12 |
| Android platform-tools (adb) | https://developer.android.com/tools/releases/platform-tools | Apache-2.0 | — |

### Optional engines that need extra toolchains

| Component | Why it fails on a clean setup | How to install |
|---|---|---|
| binwalk | PyPI only ships a dead 2.1.0 stub for Python 3.13; 2.3.4 needs a C extension build | Install rustup, then `cargo install binwalk` (v3 is Rust) |
| unblob | depends on `pyperscan`, no Windows wheel | Install VS Build Tools (2–6 GB), then `pip install unblob` |
| lldb | Apple debugger | Not useful on Windows |

These are all labelled *optional second opinion* by Craft Anything; the workflow does not need them.

## Evidence layer

| Component | Source | Licence | Notes |
|---|---|---|---|
| reverse-skill | Third-party skill package; check its own `LICENSE` | per its LICENSE | Provides `work/<case>/`, `append-evidence.ps1`, `review_case.py`. This repo ships **no part of it**. |

## AI backends (only for the rebuild layer)

Any OpenAI-compatible endpoint works. Nothing here is bundled.

| Backend | Notes |
|---|---|
| DeepSeek | `--kind openai_compatible --base-url https://api.deepseek.com` |
| OpenAI / OpenRouter / Anthropic | `--kind openai` / `openrouter` / `anthropic` |
| Local servers (llama.cpp, LM Studio, Ollama) | `--kind openai_compatible --base-url http://127.0.0.1:<port>/v1` |
| Agent CLIs (Claude Code, Codex, Gemini CLI…) | `--kind agent_cli` — **the only kind that needs a pre-installed CLI** |

## Calibrator

| Component | Source | Licence |
|---|---|---|
| Interop-Bench | Inside upstream `bench/` | MIT |

---

## Trademarks

Product names, logos and trademarks mentioned here belong to their respective owners.
Mention is descriptive and does not imply endorsement.
