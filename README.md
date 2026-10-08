# J.A.R.V.I.S. — Personal AI Desktop Assistant

A Windows desktop AI assistant with a Tkinter GUI, a text/voice command-line mode, and a guarded tool layer. The LLM (Groq or Google Gemini) can *request* actions such as opening apps, browsing the web, writing project files, or controlling a visible window, but **the host program validates every request, enforces permission switches, and asks you before acting.**

> **Platform:** Windows 10/11 is the primary target (desktop control, Excel automation, WinGet, and Edge browser control are Windows-only). The text CLI and some file/app helpers have macOS/Linux fallbacks, but the GUI features are built for Windows.

---

## Features

- **GUI (`jarvis_tkinter.py`)** — chat window with voice input (MIC), spoken replies, and per-session permission toggles (Browser Access, Desktop Access, App Monitor).
- **Two LLM providers** — Groq (default) or Google Gemini, switchable with an environment variable or CLI flag.
- **Tool calling (`assistant_tools.py`)** — the model can call: `list_apps`, `inspect_app`, `open_app`, `close_app`, `connect_code_workspace`, `read_project_file`, `write_project_file`, `run_project_check`, `prepare_project_tools`, `browse_web`, `desktop_action`, `browser_action`.
- **Project tasks** — pick a workspace folder, describe a task, and JARVIS creates files in VS Code after showing a preview. Missing tools (Python, Node.js, Git, .NET, Java, Go, Rust, Blender, Godot, VS Code) can be installed via WinGet, with a separate confirmation for each one.
- **Project checks** — python/JavaScript syntax checks, Python tests, and `dotnet` tests (each asks permission first). On failure, JARVIS can propose a fix through a per-file preview.
- **Watch Folder** — monitors the selected project folder for created, modified, and deleted files.
- **Controlled browser** — an InPrivate Microsoft Edge window driven by Playwright. Local/private network addresses are blocked.
- **Desktop control** — reads visible window titles and accessible control names, and can click, type, scroll, or press keys in an open app (confirmation required for risky actions).
- **Excel tasks** — write `A1=value` style assignments into an existing `.xlsx` / `.xlsm` workbook.
- **Process monitor (`demoi.py`)** — a small CLI that logs process start/stop events (names and PIDs only).
- **Audit log** — security-relevant events are appended to `jarvis_audit.jsonl`.

---

## Requirements

- Windows 10/11
- Python 3.10+ (with Tkinter, included in the standard python.org installer)
- A Groq API key ([console.groq.com](https://console.groq.com)) and/or a Google GenAI API key ([aistudio.google.com](https://aistudio.google.com))
- Microsoft Edge (used by the controlled browser)
- A working microphone and speakers for voice mode (optional)
- Microsoft Excel (only for the Excel task feature)
- WinGet (only for the optional tool-installation flow)

---

## Installation

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

Core packages used: `groq`, `google-genai`, `numpy`, `sounddevice`, `SpeechRecognition`, `pyttsx3`, `pywin32`, `pywinauto`, `playwright`, `pytest`.

---

## API key setup

Keys are stored outside the repository, in `%USERPROFILE%\.jarvis\`. Use hidden-input setup:

```bash
# Groq (default provider)
python foog.py --setup-api-key

# Google Gemini
python foog.py --setup-google-api-key
```

You can also provide keys through environment variables (`GROQ_API_KEY`, `GOOGLE_API_KEY`). A saved key file takes priority over the environment variable. **Never commit API keys to GitHub.**

---

## Enabling actions (important)

For safety, **all side-effect actions are locked by default.** Without the switch below, JARVIS can chat but cannot open apps, browse, write files, or control the desktop.

```powershell
# PowerShell — set for the current session, then start JARVIS
$env:JARVIS_ENABLE_COMMANDS = "1"
python jarvis_tkinter.py
```

```bat
:: Command Prompt
set JARVIS_ENABLE_COMMANDS=1
python jarvis_tkinter.py
```

Restart JARVIS after changing this variable. To block every action regardless of other settings, set `JARVIS_DISABLE_COMMANDS=1`.

---

## Running

### GUI (recommended)

```bash
python jarvis_tkinter.py
```

The window starts fullscreen. Type a message and press **SEND**, or click **MIC** to speak.

| Control | What it does |
|---|---|
| **SEND / MIC** | Send typed text / record a voice message |
| **VOICE RESPONSE** | Toggle spoken replies |
| **BROWSER ACCESS** | Allow JARVIS to drive its controlled browser for this session |
| **DESKTOP ACCESS** | Allow JARVIS to read window titles/control names and act in open apps |
| **APP MONITOR** | Show the current foreground app |
| **NEW TASK** | Describe a coding/project task for JARVIS to build in your workspace |
| **WATCH FOLDER** | Monitor the selected project folder for file changes |

Permission toggles reset every session. Every action still shows a confirmation dialog before it runs.

### Command line

```bash
python foog.py                        # text mode, Groq
python foog.py --mode voice           # voice mode
python foog.py --provider google      # use Gemini
```

Slash commands available in the CLI:

`/open`, `/close`, `/run`, `/terminal`, `/system`, `/write`, `/write-code`, `/concept`, `/type-message`, `/read-messages`, `/plugins`, `/app`, `/setup`, `/browse`, `/download`, `/scan`, `/api-key`, `/voice`, `/text`

`/run` only accepts a host allowlist of commands, and `/scan` is disabled unless `JARVIS_ENABLE_SCANS=1` is set. Use it only on systems you are authorized to test.

### Other entry points

| File | Purpose |
|---|---|
| `main.py` | Simple Groq CLI assistant (`python main.py --mode text\|voice`) |
| `javi.py` | Earlier CLI variant that can also serve a Flask web UI (`python javi.py --web --port 5000`) |
| `demoi.py` | Windows app manager and process monitor (`python demoi.py`; commands: `apps`, `open`, `close`, `install`, `processes`, `monitor`, `stop-monitor`) |

---

## Example usage

- "Open Notepad." → confirmation dialog → Notepad opens.
- "Search the web for Python tutorials." → opens in the controlled Edge window (needs Browser Access).
- "Open VS Code and build a to-do list app in Python." → you choose a project folder, JARVIS prepares tools, shows a file preview, and saves only after you confirm.
- "Check my project for errors." → JARVIS proposes a check and asks permission before running it.
- "What apps do I have open?" → works only while Desktop Access is ON.

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `JARVIS_ENABLE_COMMANDS` | `0` | Unlocks guarded actions (apps, browser, writes, desktop control) |
| `JARVIS_DISABLE_COMMANDS` | `0` | Blocks all actions when set to `1` |
| `JARVIS_ENABLE_SCANS` | `0` | Enables the `/scan` feature |
| `JARVIS_LLM_PROVIDER` | `groq` | `groq` or `google` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model name |
| `GOOGLE_MODEL` | see `foog.py` | Gemini model name |
| `GROQ_API_KEY` / `GOOGLE_API_KEY` | — | API keys (alternative to the saved key files) |
| `JARVIS_API_KEY_FILE` | `~/.jarvis/groq_api_key` | Groq key file location |
| `JARVIS_GOOGLE_API_KEY_FILE` | `~/.jarvis/google_api_key` | Google key file location |
| `JARVIS_WORKSPACE_ROOT` | current directory | Folder that file operations are restricted to |
| `JARVIS_AUDIT_LOG` | `jarvis_audit.jsonl` | Audit log path |
| `JARVIS_MESSAGE_LOG` | `<workspace>/jarvis_messages.jsonl` | Local message log |
| `JARVIS_COMMAND_PERMISSION_PATH` | `~/.jarvis/approved_commands.json` | Saved "always allow" commands |
| `JARVIS_CODE_EDITOR` | VS Code | Editor used to open written files |
| `JARVIS_<APP>_PATH` | — | Custom executable path for an app (e.g. `JARVIS_EXCEL_PATH`) |
| `JARVIS_COMMAND_TIMEOUT` | `60` | Allowlisted command timeout in seconds (1–300) |
| `JARVIS_SCAN_TIMEOUT` | `10` | Scan timeout in seconds (3–30) |
| `JARVIS_SAMPLE_RATE` / `JARVIS_SILENCE_THRESHOLD` / `JARVIS_SILENCE_DURATION` / `JARVIS_MAX_RECORD_SECONDS` | `16000` / `350` / `1.2` / `20` | Voice recording tuning |
| `JARVIS_TTS_RATE` | `175` | Speech speed |
| `JARVIS_PROCESS_LOG` | `%LOCALAPPDATA%\Jarvis\process_events.jsonl` | Process monitor log |

---

## Safety model

- Actions are **off by default** and need `JARVIS_ENABLE_COMMANDS=1`.
- Every open/close/write/click/type action needs confirmation; the model cannot approve its own requests.
- File reads and writes are limited to the selected workspace, and writes show a preview first.
- Tool-call limits per request: 10 rounds, 20 calls.
- The controlled browser blocks private, loopback, and link-local addresses, and does not download files or submit forms for the model.
- Installers are never run silently; WinGet installs ask for confirmation per package.
- No screenshots, clipboard contents, or keystrokes are monitored.
- Window titles, control names, and file contents you approve are sent to the configured AI provider for that request.

---

## Tests

```bash
pytest
```

Tests cover the tool-calling loop, command confirmation flow, the process monitor, desktop controller, project monitor, and Excel parsing. Some tests need Windows.

---

## Build a standalone .exe (optional)

```bash
pip install pyinstaller
pyinstaller jarvis_tkinter.spec
```

The executable is created in `dist/`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Actions are locked" | Set `JARVIS_ENABLE_COMMANDS=1` and restart JARVIS |
| `ModuleNotFoundError` | Activate the virtual environment and run `pip install -r requirements.txt` |
| Voice mode fails | Install `SpeechRecognition` and `pyttsx3`, and check your microphone permissions |
| Google provider fails | Install `google-genai` and run `python foog.py --setup-google-api-key` |
| Browser actions fail | Make sure Microsoft Edge is installed and `playwright` is installed |
| Desktop control fails | Install `pywin32` and `pywinauto`; run only on Windows |

---

## Privacy note

`jarvis_audit.jsonl` and `jarvis_messages.jsonl` are local logs and may contain file paths and usernames. They are listed in `.gitignore` — do not commit them.

## License

Add your license here (for example MIT).
