import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

import foog


DEFAULT_PROCESS_LOG = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    / "Jarvis"
    / "process_events.jsonl"
)
PROCESS_LOG_PATH = Path(
    os.environ.get("JARVIS_PROCESS_LOG", DEFAULT_PROCESS_LOG)
).expanduser()


def process_snapshot():
    if os.name != "nt":
        raise RuntimeError("Process monitoring is currently supported on Windows only.")

    result = subprocess.run(
        ["tasklist.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or f"tasklist exited with code {result.returncode}"
        raise RuntimeError(f"Could not read the Windows process list: {detail}")

    processes = set()
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) < 2:
            continue
        try:
            process_id = int(row[1])
        except ValueError:
            continue
        processes.add((process_id, row[0]))
    return processes


def process_changes(previous, current):
    events = []
    for event_type, changed in (
        ("started", current - previous),
        ("stopped", previous - current),
    ):
        for process_id, name in sorted(changed, key=lambda item: (item[1].casefold(), item[0])):
            events.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": event_type,
                    "pid": process_id,
                    "name": name,
                }
            )
    return events


class ProcessMonitor:
    def __init__(self, log_path=PROCESS_LOG_PATH, interval=2.0, snapshot=process_snapshot):
        self.log_path = Path(log_path)
        self.interval = interval
        self.snapshot = snapshot
        self._stop_event = threading.Event()
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return "Process monitoring is already running."

        previous = self.snapshot()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watch,
            args=(previous,),
            daemon=True,
            name="jarvis-process-monitor",
        )
        self._thread.start()
        return (
            "Monitoring process names and PIDs. "
            f"Events will be saved to {self.log_path}. Use 'stop-monitor' to stop."
        )

    def stop(self):
        if not self.running:
            return "Process monitoring is not running."
        self._stop_event.set()
        self._thread.join(timeout=max(5.0, self.interval + 1.0))
        if self._thread.is_alive():
            raise RuntimeError("Process monitoring did not stop before the timeout.")
        return "Process monitoring stopped."

    def _watch(self, previous):
        while not self._stop_event.wait(self.interval):
            try:
                current = self.snapshot()
                events = process_changes(previous, current)
                if events:
                    self.log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.log_path.open("a", encoding="utf-8") as log_file:
                        for event in events:
                            log_file.write(json.dumps(event) + "\n")
                            print(
                                f"[{event['event'].upper()}] "
                                f"{event['name']} (PID {event['pid']})"
                            )
                previous = current
            except (OSError, RuntimeError) as exc:
                print(f"Process monitoring stopped because of an error: {exc}")
                self._stop_event.set()
                return


def confirm_action(description):
    try:
        answer = input(f"{description} Type YES to continue: ").strip().casefold()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return False
    if answer != "yes":
        print("Cancelled.")
        return False
    return True


def install_application(app_name):
    if not app_name or len(app_name) > 120:
        print("Name an app using between 1 and 120 characters.")
        return
    if not confirm_action(f"Search the official WinGet source for {app_name}?"):
        return

    winget = shutil.which("winget.exe") or shutil.which("winget")
    if winget is None:
        print("WinGet is unavailable. Opening Microsoft Store results instead.")
        print(foog.open_authorized_store(f"search {app_name} yes"))
        return

    try:
        search = subprocess.run(
            [winget, "search", "--query", app_name, "--source", "winget"],
            check=False,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"WinGet search failed: {exc}")
        return
    if search.returncode:
        print(f"WinGet search failed with exit code {search.returncode}.")
        return

    try:
        package_id = input("Enter the exact package ID shown above (or press Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}", package_id):
        print("Cancelled: the package ID is empty or invalid.")
        return

    try:
        details = subprocess.run(
            [winget, "show", "--id", package_id, "--exact", "--source", "winget"],
            check=False,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Could not retrieve package details: {exc}")
        return
    if details.returncode:
        print(f"WinGet could not verify that package (exit code {details.returncode}).")
        return

    if not confirm_action(
        f"Install exact package {package_id} from WinGet? Review its publisher/details above"
    ):
        return
    try:
        installation = subprocess.run(
            [
                winget,
                "install",
                "--id",
                package_id,
                "--exact",
                "--source",
                "winget",
                "--interactive",
            ],
            check=False,
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"WinGet installation failed: {exc}")
        return
    if installation.returncode:
        print(f"Installation failed with exit code {installation.returncode}.")
    else:
        print(f"WinGet completed installation of {package_id}.")


def show_help():
    print(
        "Commands:\n"
        "  apps                     Show approved app names\n"
        "  open <app>               Open an approved app after confirmation\n"
        "  close <app>              Force-close an approved app after confirmation\n"
        "  install <app name>       Find and install a reviewed WinGet package\n"
        "  processes                List current process names and PIDs\n"
        "  monitor                  Start visible background process monitoring\n"
        "  stop-monitor             Stop process monitoring\n"
        "  help                     Show this help\n"
        "  quit                     Exit and stop monitoring\n\n"
        "App open/close actions require JARVIS_ENABLE_COMMANDS=1. Installing "
        "requires an exact package ID and a separate confirmation after review."
    )


def dispatch_command(command, monitor):
    action, _, argument = command.strip().partition(" ")
    action = action.casefold()
    argument = argument.strip()

    if action in {"quit", "exit"}:
        return False
    if action == "help":
        show_help()
    elif action == "apps":
        print("Approved apps:", ", ".join(sorted(foog.ALLOWED_APPLICATIONS)))
    elif action == "open" and argument:
        if confirm_action(f"Open {argument}?"):
            print(foog.open_authorized_application(f"{argument} yes"))
    elif action == "close" and argument:
        if confirm_action(
            f"Force-close every running instance of {argument}? Unsaved work may be lost."
        ):
            print(foog.close_authorized_application(f"{argument} yes"))
    elif action == "install" and argument:
        install_application(argument)
    elif action == "processes":
        for process_id, name in sorted(
            process_snapshot(), key=lambda item: (item[1].casefold(), item[0])
        ):
            print(f"{name} (PID {process_id})")
    elif action == "monitor" and not argument:
        print(monitor.start())
    elif action == "stop-monitor" and not argument:
        print(monitor.stop())
    else:
        print("Unknown command or missing app name. Type 'help' for available commands.")
    return True


def main():
    if os.name != "nt":
        raise RuntimeError("This app manager is currently supported on Windows only.")

    monitor = ProcessMonitor()
    print("Local Windows app assistant. It does not record keystrokes or window contents.")
    show_help()
    try:
        while True:
            try:
                command = input("\nassistant> ")
            except EOFError:
                break
            if not dispatch_command(command, monitor):
                break
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if monitor.running:
            print(monitor.stop())


if __name__ == "__main__":
    main()
