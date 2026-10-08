import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

try:
    from groq import Groq
except ImportError:  # pragma: no cover
    Groq = None

try:
    import pyttsx3
    import speech_recognition as sr
except ImportError:  # pragma: no cover
    pyttsx3 = None
    sr = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


DEFAULT_MODEL = os.getenv("GROQ_MODEL", "groq/compound")
WORKSPACE_ROOT = Path(os.getenv("JARVIS_WORKSPACE_ROOT", Path.cwd())).resolve()
AUDIT_LOG_PATH = Path(os.getenv("JARVIS_AUDIT_LOG", WORKSPACE_ROOT / "jarvis_audit.jsonl")).resolve()
MESSAGE_LOG_PATH = Path(os.getenv("JARVIS_MESSAGE_LOG", WORKSPACE_ROOT / "jarvis_messages.jsonl")).resolve()
APP_REGISTRY = {
    "chrome": {"windows": ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"], "darwin": ["open", "-a", "Google Chrome"], "linux": ["google-chrome"]},
    "firefox": {"windows": ["C:\\Program Files\\Mozilla Firefox\\firefox.exe"], "darwin": ["open", "-a", "Firefox"], "linux": ["firefox"]},
    "vscode": {"windows": ["C:\\Program Files\\Microsoft VS Code\\Code.exe"], "darwin": ["code"], "linux": ["code"]},
    "calculator": {"windows": ["calc.exe"], "darwin": ["open", "-a", "Calculator"], "linux": ["gnome-calculator"]},
    "notepad": {"windows": ["notepad.exe"], "darwin": ["open", "-a", "TextEdit"], "linux": ["gedit"]},
    "terminal": {"windows": ["cmd.exe"], "darwin": ["open", "-a", "Terminal"], "linux": ["gnome-terminal"]},
    "files": {"windows": [r"explorer.exe"], "darwin": ["open", "."], "linux": ["xdg-open", "."]},
}
SYSTEM_PLUGINS = {
    "app_control": "open and close approved desktop applications",
    "messaging": "type and read local assistant messages",
    "code_writer": "write code into workspace files",
    "browser_search": "search the web with a safe, user-approved browser flow",
    "workspace_files": "create, read, rename, and delete files inside the workspace only",
}

MCP_CONNECTORS = {
    "mcp_servers": "No external MCP servers are configured. Only built-in, allowlisted local tools are active.",
    "third_party_apps": "No third-party app connectors are connected by default; adding them requires explicit opt-in.",
    "whole_system_access": "Full system integration is disabled unless you explicitly enable command execution and confirm each action.",
}
CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".java", ".c", ".cpp", ".cs", ".go", ".rs"}

SAFE_COMMANDS = {
    "whoami",
    "hostname",
    "pwd",
    "ls",
    "dir",
    "echo",
    "python",
    "python3",
    "pip",
    "ipconfig",
    "hostname",
    "tasklist",
    "getmac",
    "systeminfo",
    "tree",
    "where",
}
DANGEROUS_TOKENS = ("|", ";", "&&", "||", ">", "<", "rm ", "del /s", "format ", "shutdown /s", "net user", "ssh ", "scp ", "curl http", "powershell", "cmd /c")


def audit_event(event: str, **details):
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event}
        record.update({k: v for k, v in details.items() if k not in {"api_key", "password", "token", "secret", "prompt"}})
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key, "").strip().lower()
    return value in {"1", "true", "yes", "on"} if value else default


def confirm_action(description: str) -> bool:
    print(f"This action requires confirmation: {description}")
    try:
        answer = input("Type YES to confirm: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    audit_event("confirmation", description=description, confirmed=answer == "yes")
    return answer == "yes"


def get_groq_client():
    if Groq is None:
        raise RuntimeError("The groq package is not installed. Install dependencies from requirements.txt.")
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Export it in your shell or environment before using Groq.")
    return Groq(api_key=api_key)


def make_safe_path(path_text: str, base: Path | None = None) -> Path | None:
    base_root = (base or WORKSPACE_ROOT).resolve()
    requested = Path(path_text).expanduser()
    candidate = (base_root / requested).resolve() if not requested.is_absolute() else requested.resolve()
    try:
        candidate.relative_to(base_root)
        return candidate
    except ValueError:
        return None


def map_platform() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "windows"
    if system == "darwin":
        return "darwin"
    return "linux"


def is_application_allowed(app_name: str) -> bool:
    return app_name.lower() in APP_REGISTRY


def find_executable(executable: str) -> str | None:
    if shutil.which(executable):
        return shutil.which(executable)
    return None


def open_application(app_name: str, target_url: str | None = None):
    if not env_bool("JARVIS_ENABLE_COMMANDS", False):
        return "Application launching is disabled. Set JARVIS_ENABLE_COMMANDS=1 to enable it."
    app_key = app_name.strip().lower()
    if app_key not in APP_REGISTRY:
        return f"Application '{app_name}' is not in the approved registry."
    if app_key == "chrome" and target_url:
        return browser_search(target_url, use_url=True)
    platform_key = map_platform()
    command = APP_REGISTRY[app_key].get(platform_key)
    if not command:
        return f"Application '{app_name}' is not configured for {platform_key}."
    try:
        if app_key in {"files", "notepad", "terminal", "calculator"} and platform_key == "windows":
            subprocess.Popen(command, shell=False)
        elif app_key == "vscode" and platform_key == "windows":
            subprocess.Popen(command, shell=False)
        elif app_key == "chrome" and platform_key == "windows":
            subprocess.Popen(command, shell=False)
        else:
            subprocess.Popen(command, shell=False)
        audit_event("application_opened", application=app_key)
        return f"Opened {app_key}."
    except FileNotFoundError:
        return f"Could not find the executable for '{app_key}'."
    except OSError as exc:
        return f"Failed to open '{app_key}': {exc}"


def close_application(app_name: str):
    if not env_bool("JARVIS_ENABLE_COMMANDS", False):
        return "Application closing is disabled. Set JARVIS_ENABLE_COMMANDS=1 to enable it."
    app_key = app_name.strip().lower()
    if app_key not in {"chrome", "firefox", "vscode", "notepad", "terminal", "calculator"}:
        return f"Application '{app_key}' cannot be closed through the approved layer."
    if not confirm_action(f"close {app_key}"):
        return f"Closing {app_key} was cancelled."
    try:
        if map_platform() == "windows":
            if app_key == "chrome":
                subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, text=True, check=False)
            elif app_key == "firefox":
                subprocess.run(["taskkill", "/F", "/IM", "firefox.exe"], capture_output=True, text=True, check=False)
            elif app_key == "vscode":
                subprocess.run(["taskkill", "/F", "/IM", "Code.exe"], capture_output=True, text=True, check=False)
            elif app_key == "notepad":
                subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True, text=True, check=False)
            elif app_key == "terminal":
                subprocess.run(["taskkill", "/F", "/IM", "cmd.exe"], capture_output=True, text=True, check=False)
            else:
                subprocess.run(["taskkill", "/F", "/IM", "calc.exe"], capture_output=True, text=True, check=False)
        else:
            subprocess.run(["pkill", "-f", app_key], capture_output=True, text=True, check=False)
        audit_event("application_closed", application=app_key)
        return f"Closed {app_key}."
    except Exception as exc:
        return f"Failed to close '{app_key}': {exc}"


def type_message(message: str, recipient: str = "local"):
    """Store a message locally; this plugin never sends messages to a third party."""
    text = message.strip()
    if not text:
        return "Please provide a message to type."
    if len(text) > 2000:
        return "Messages are limited to 2000 characters."
    try:
        MESSAGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with MESSAGE_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": time.time(), "recipient": recipient, "message": text}) + "\n")
        audit_event("message_typed", recipient=recipient, length=len(text))
        return f"Typed local message for {recipient}: {text}"
    except OSError as exc:
        return f"Could not store the message: {exc}"


def read_messages(limit: int = 10):
    if not MESSAGE_LOG_PATH.exists():
        return "No messages have been typed."
    try:
        records = []
        for line in MESSAGE_LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not records:
            return "No messages have been typed."
        return "\n".join(f"{item.get('recipient', 'local')}: {item.get('message', '')}" for item in records)
    except OSError as exc:
        return f"Could not read messages: {exc}"


def write_code(file_name: str, content: str):
    target = Path(file_name.strip())
    if target.suffix.lower() not in CODE_EXTENSIONS:
        return "Code writing requires a supported source-code file extension."
    result = create_file(str(target), content)
    if result.startswith("Created file:"):
        audit_event("code_written", path=str(target))
    return result


def list_plugins():
    plugin_lines = ["Available system plugins:"]
    plugin_lines.extend(f"- {name}: {description}" for name, description in SYSTEM_PLUGINS.items())
    plugin_lines.append("")
    plugin_lines.append("MCP / third-party connectors:")
    plugin_lines.extend(f"- {name}: {description}" for name, description in MCP_CONNECTORS.items())
    return "\n".join(plugin_lines)


def list_directory(path_text: str | None = None):
    base = Path(path_text).expanduser() if path_text else WORKSPACE_ROOT
    valid_base = make_safe_path(str(base), WORKSPACE_ROOT)
    if valid_base is None:
        return "Access denied. The requested folder must remain inside the workspace."
    if not valid_base.exists():
        return f"Folder not found: {valid_base}"
    items = sorted([p.name for p in valid_base.iterdir()])
    return "\n".join(items) if items else "Directory is empty."


def create_folder(folder_name: str):
    if not folder_name:
        return "Please provide a folder name or path."
    safe_target = make_safe_path(folder_name, WORKSPACE_ROOT)
    if safe_target is None:
        return "Folder creation is blocked outside the workspace."
    safe_target.mkdir(parents=True, exist_ok=True)
    audit_event("folder_created", path=str(safe_target))
    return f"Created folder: {safe_target}"


def create_file(file_name: str, content: str = ""):
    target = make_safe_path(file_name, WORKSPACE_ROOT)
    if target is None:
        return "File creation is blocked outside the workspace."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    audit_event("file_created", path=str(target))
    return f"Created file: {target}"


def read_file(file_name: str):
    target = make_safe_path(file_name, WORKSPACE_ROOT)
    if target is None:
        return "Reading outside the workspace is blocked."
    if not target.exists():
        return f"File not found: {target}"
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Binary or unsupported file format: {target}"
    return text[:5000] if len(text) > 5000 else text


def rename_file(old_name: str, new_name: str):
    if not confirm_action(f"rename {old_name} to {new_name}"):
        return "Rename cancelled."
    old_target = make_safe_path(old_name, WORKSPACE_ROOT)
    new_target = make_safe_path(new_name, WORKSPACE_ROOT)
    if old_target is None or new_target is None:
        return "Renaming outside the workspace is blocked."
    if not old_target.exists():
        return f"Source file not found: {old_target}"
    old_target.rename(new_target)
    audit_event("file_renamed", source=str(old_target), target=str(new_target))
    return f"Renamed {old_target.name} to {new_target.name}."


def delete_file(path_text: str):
    if not confirm_action(f"delete {path_text}"):
        return "Deletion cancelled."
    target = make_safe_path(path_text, WORKSPACE_ROOT)
    if target is None:
        return "Deleting outside the workspace is blocked."
    if not target.exists():
        return f"File not found: {target}"
    target.unlink()
    audit_event("file_deleted", path=str(target))
    return f"Deleted {target}."


def browser_search(query: str, use_url: bool = False):
    if use_url:
        url = query.strip() if query.startswith("http") else f"https://{query}"
        webbrowser.open(url, new=2)
        return f"Opened {url} in the browser."
    url = "https://www.google.com/search?q=" + quote_plus(query)
    webbrowser.open(url, new=2)
    return f"Searching the web for: {query}"


def system_information():
    info = {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }
    if psutil is not None:
        info["cpu_percent"] = psutil.cpu_percent(interval=None)
        info["memory_total_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 2)
        info["memory_used_gb"] = round(psutil.virtual_memory().used / (1024 ** 3), 2)
        info["disk_total_gb"] = round(shutil.disk_usage(".").total / (1024 ** 3), 2)
        info["disk_free_gb"] = round(shutil.disk_usage(".").free / (1024 ** 3), 2)
    return json.dumps(info, indent=2)


def process_information():
    if psutil is None:
        return "Process listing is unavailable because psutil is not installed."
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent"]):
        try:
            processes.append({"pid": proc.info["pid"], "name": proc.info["name"], "cpu_percent": round(proc.info.get("cpu_percent") or 0, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    processes.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return json.dumps(processes[:20], indent=2)


def run_command(command: str):
    if not env_bool("JARVIS_ENABLE_COMMANDS", False):
        return "Command execution is disabled. Set JARVIS_ENABLE_COMMANDS=1 to enable it."
    command = command.strip()
    if not command:
        return "Please provide a command to run."
    if any(token in command for token in DANGEROUS_TOKENS):
        return "This command contains blocked shell operators or unsafe actions. Use a direct, simple command only."
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return f"Command parsing failed: {exc}"
    if not parts:
        return "No command was supplied."
    executable = parts[0].lower()
    if executable not in SAFE_COMMANDS and not executable.endswith(".py"):
        return f"Command '{executable}' is not an approved safe command."
    if not confirm_action(f"execute '{command}'"):
        return "Command execution was cancelled."
    try:
        completed = subprocess.run(parts, capture_output=True, text=True, timeout=30, check=False)
        output = (completed.stdout or "") + (completed.stderr or "")
        if not output.strip():
            output = "[no output]"
        audit_event("command_executed", command=command, return_code=completed.returncode)
        return f"Exit code: {completed.returncode}\n{output[:8000]}"
    except FileNotFoundError:
        return f"Executable not found: {parts[0]}"
    except subprocess.TimeoutExpired:
        return "The command timed out after 30 seconds."


def explain_error(error_text: str):
    return (
        "I can help explain the error, but I cannot guarantee the underlying cause without the exact traceback and context. "
        f"The relevant error appears to be: {error_text}. "
        "Check the stack frame where it occurred, verify the file path, dependencies, and required arguments, and then retry with the smallest failing example."
    )


def generate_action_from_text(user_text: str, session: dict):
    lowered = user_text.strip().lower()
    if not lowered:
        return {"action": "noop", "target": None}

    if lowered in {"quit", "exit", "goodbye", "bye"}:
        return {"action": "quit", "target": None}

    if lowered.startswith("open "):
        target = lowered[5:].strip()
        if not target:
            return {"action": "open_application", "target": "vscode"}
        if target in {"chrome", "firefox", "vscode", "calculator", "notepad", "terminal", "files"}:
            return {"action": "open_application", "target": target}
        if target.startswith("http://") or target.startswith("https://"):
            return {"action": "browser_search", "target": target, "query": target}
        return {"action": "open_application", "target": target}

    if lowered.startswith("close "):
        target = lowered[6:].strip()
        return {"action": "close_application", "target": target}

    if lowered in {"list plugins", "show plugins", "plugin controls"}:
        return {"action": "list_plugins", "target": None}

    if lowered.startswith("type message ") or lowered.startswith("send message "):
        prefix = "type message " if lowered.startswith("type message ") else "send message "
        message = user_text[len(prefix):].strip()
        return {"action": "type_message", "target": message}

    if lowered in {"read messages", "read message", "show messages", "check messages"}:
        return {"action": "read_messages", "target": None}

    if lowered.startswith("write code "):
        request = user_text[len("write code "):].strip()
        if "|" not in request:
            return {"action": "write_code", "target": request, "content": ""}
        file_name, content = request.split("|", 1)
        return {"action": "write_code", "target": file_name.strip(), "content": content.lstrip()}

    if lowered.startswith("create a folder") or lowered.startswith("make a folder") or lowered.startswith("create folder"):
        folder = re.sub(r"^(create|make) (a )?(folder|directory)\s*", "", lowered, flags=re.I)
        return {"action": "create_folder", "target": folder or "new_folder"}

    if lowered.startswith("create a file") or lowered.startswith("create file") or lowered.startswith("make a file"):
        file_name = re.sub(r"^(create|make) (a )?(file|python file)\s*", "", lowered, flags=re.I)
        if not file_name:
            file_name = "new_file.py"
        if not file_name.endswith((".py", ".txt", ".md", ".json", ".csv")):
            if "python" in lowered:
                file_name = f"{file_name}.py" if not file_name.endswith(".py") else file_name
            else:
                file_name = f"{file_name}.txt" if not file_name.endswith(".txt") else file_name
        return {"action": "create_file", "target": file_name, "content": ""}

    if re.search(r"read (this|the) file|read .*\.txt|read .*\.py|read .*\.md", lowered):
        file_name = session.get("last_file") or "README.md"
        return {"action": "read_file", "target": file_name}

    if re.search(r"rename (this|the) file|rename .* to .*", lowered):
        match = re.search(r"rename\s+(?:this|the)?\s*file\s+(?:to\s+)?(.+)", lowered)
        if match:
            target = match.group(1).strip()
            return {"action": "rename_file", "target": session.get("last_file") or "example.txt", "new_name": target}

    if lowered.startswith("search the web") or "search for" in lowered:
        query = re.sub(r"^(search the web for|search for)\s*", "", lowered, flags=re.I)
        return {"action": "browser_search", "target": query}

    if "system information" in lowered or "show system info" in lowered or "system status" in lowered:
        return {"action": "system_information", "target": None}

    if "running applications" in lowered or "processes" in lowered or "list running" in lowered:
        return {"action": "process_information", "target": None}

    if "create a python file" in lowered or "make a python file" in lowered:
        return {"action": "create_file", "target": "new_script.py", "content": "print('Hello from JARVIS')\n"}

    if "run this python program" in lowered or lowered.startswith("run "):
        target = re.sub(r"^(run|execute)\s+", "", lowered, flags=re.I)
        return {"action": "run_command", "target": target}

    if "read this text file" in lowered or lowered.startswith("read "):
        file_name = re.sub(r"^read\s+", "", lowered, flags=re.I)
        return {"action": "read_file", "target": file_name}

    if "explain this error" in lowered:
        return {"action": "explain_error", "target": "Please share the exact traceback or error message."}

    if "open my project folder" in lowered or "open project folder" in lowered:
        return {"action": "open_folder", "target": str(WORKSPACE_ROOT)}

    return {"action": "chat", "target": user_text}


def open_folder(path_text: str):
    target = Path(path_text).expanduser().resolve()
    if not target.exists():
        return f"Folder not found: {target}"
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer.exe", str(target)], shell=False)
    else:
        subprocess.Popen(["xdg-open", str(target)] if sys.platform.startswith("linux") else ["open", str(target)], shell=False)
    return f"Opened folder: {target}"


def execute_action(action: dict, session: dict):
    kind = action.get("action")
    target = action.get("target")
    if kind == "noop":
        return "I’m ready when you are."
    if kind == "quit":
        return "Goodbye."
    if kind == "open_application":
        if target is None:
            target = "vscode"
        if target.startswith("http://") or target.startswith("https://"):
            return browser_search(target, use_url=True)
        return open_application(str(target))
    if kind == "close_application":
        return close_application(str(target or "chrome"))
    if kind == "list_plugins":
        return list_plugins()
    if kind == "type_message":
        return type_message(str(target or ""))
    if kind == "read_messages":
        return read_messages()
    if kind == "write_code":
        content = action.get("content", "")
        created = write_code(str(target or "new_script.py"), content)
        if created.startswith("Created file:"):
            session["last_file"] = str(target or "new_script.py")
        return created
    if kind == "create_folder":
        return create_folder(str(target or "new_folder"))
    if kind == "create_file":
        content = action.get("content", "")
        created = create_file(str(target or "new_file.txt"), content)
        session["last_file"] = str(target or "new_file.txt")
        return created
    if kind == "read_file":
        file_text = read_file(str(target or "README.md"))
        session["last_file"] = str(target or "README.md")
        return file_text
    if kind == "rename_file":
        return rename_file(str(target or "old_name.txt"), str(action.get("new_name") or "new_name.txt"))
    if kind == "delete_file":
        return delete_file(str(target or ""))
    if kind == "browser_search":
        if target and (target.startswith("http://") or target.startswith("https://")):
            return browser_search(target, use_url=True)
        return browser_search(str(target or "python tutorials"))
    if kind == "system_information":
        return system_information()
    if kind == "process_information":
        return process_information()
    if kind == "run_command":
        return run_command(str(target or ""))
    if kind == "open_folder":
        return open_folder(str(target or str(WORKSPACE_ROOT)))
    if kind == "explain_error":
        return explain_error(str(target or "No error text provided."))
    if kind == "chat":
        prompt = action.get("target") or ""
        return chat_with_groq(prompt)
    return "I’m not sure which action you want. Try a simpler command like 'Open VS Code' or 'Show system information'."


def chat_with_groq(user_text: str):
    try:
        client = get_groq_client()
    except RuntimeError as exc:
        return str(exc)
    system_prompt = (
        "You are a concise personal AI assistant. Reply in plain language, answer directly, and never claim you executed a real system action. "
        "Keep answers short, useful, and professional."
    )
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        temperature=0.5,
        max_tokens=450,
    )
    return response.choices[0].message.content.strip()


class VoiceIO:
    def __init__(self):
        if pyttsx3 is None or sr is None:
            raise RuntimeError("Voice mode requires pyttsx3 and SpeechRecognition. Install them from the project requirements.")
        self.recognizer = sr.Recognizer()
        self.engine = pyttsx3.init()

    def speak(self, text: str):
        if not text:
            return
        print(f"Assistant: {text}")
        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception:
            print("Voice output unavailable.")

    def listen(self):
        with sr.Microphone() as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
            print("Listening...")
            audio = self.recognizer.listen(source, timeout=10, phrase_time_limit=5)
        try:
            text = self.recognizer.recognize_google(audio)
            print(f"You: {text}")
            return text
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            return ""


def parse_voice_action(text: str):
    lowered = text.strip().lower()
    if not lowered:
        return {"action": "noop"}
    if lowered.startswith("open "):
        app = lowered.replace("open ", "", 1).strip()
        return {"action": "open_application", "target": app}
    if lowered.startswith("close "):
        app = lowered.replace("close ", "", 1).strip()
        return {"action": "close_application", "target": app}
    if lowered.startswith("type message ") or lowered.startswith("send message "):
        prefix = "type message " if lowered.startswith("type message ") else "send message "
        return {"action": "type_message", "target": text[len(prefix):].strip()}
    if lowered in {"read messages", "read message", "show messages", "check messages"}:
        return {"action": "read_messages"}
    if lowered.startswith("write code "):
        request = text[len("write code "):].strip()
        if "|" not in request:
            return {"action": "write_code", "target": request, "content": ""}
        file_name, content = request.split("|", 1)
        return {"action": "write_code", "target": file_name.strip(), "content": content.lstrip()}
    if "system information" in lowered or "show system info" in lowered:
        return {"action": "system_information"}
    if "search" in lowered:
        return {"action": "browser_search", "target": lowered.replace("search", "", 1).strip() or "python tutorials"}
    if "read" in lowered:
        return {"action": "read_file", "target": "README.md"}
    return {"action": "chat", "target": text}


def main():
    parser = argparse.ArgumentParser(description="AI personal assistant with Groq and controlled tool execution")
    parser.add_argument("--mode", choices=["text", "voice"], default="text", help="Text or voice interaction mode")
    args = parser.parse_args()

    session = {"last_file": None}
    if args.mode == "voice":
        try:
            voice = VoiceIO()
        except RuntimeError as exc:
            print(exc)
            return
        print("Voice mode ready. Say 'stop' to end.")
        while True:
            text = voice.listen()
            if not text:
                continue
            if text.lower() in {"stop", "stop listening", "exit", "quit"}:
                voice.speak("Goodbye.")
                break
            action = parse_voice_action(text)
            result = execute_action(action, session)
            voice.speak(str(result)[:500])
        return

    print("AI System Assistant initialized.")
    print("Examples: 'Open VS Code', 'Create a Python file', 'Show system information', 'Search the web for Python tutorials'.")
    while True:
        try:
            user_text = input("You: ").strip()
        except EOFError:
            print("\nSession ended.")
            break
        if not user_text:
            continue
        if user_text.lower() in {"quit", "exit", "bye", "goodbye"}:
            print("Assistant: Goodbye!")
            break
        action = generate_action_from_text(user_text, session)
        if action.get("action") == "chat":
            print(f"Assistant: {chat_with_groq(user_text)}")
            continue
        result = execute_action(action, session)
        print(f"Assistant: {result}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting gracefully.")
    except Exception as exc:  # pragma: no cover
        print(f"Unhandled error: {exc}")
