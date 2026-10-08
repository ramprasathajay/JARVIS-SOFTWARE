import json
import time
from unittest.mock import patch

import demoi


def test_process_changes_records_starts_stops_and_pid_reuse():
    events = demoi.process_changes(
        {(10, "before.exe"), (20, "still.exe")},
        {(10, "after.exe"), (30, "new.exe"), (20, "still.exe")},
    )

    assert [(event["event"], event["pid"], event["name"]) for event in events] == [
        ("started", 10, "after.exe"),
        ("started", 30, "new.exe"),
        ("stopped", 10, "before.exe"),
    ]
    assert all("timestamp" in event for event in events)


def test_process_monitor_writes_only_process_event_details(tmp_path):
    snapshots = iter(
        [
            {(10, "before.exe")},
            {(10, "after.exe"), (20, "new.exe")},
        ]
    )

    def snapshot():
        return next(snapshots, {(10, "after.exe"), (20, "new.exe")})

    log_path = tmp_path / "logs" / "process_events.jsonl"
    monitor = demoi.ProcessMonitor(log_path, interval=0.01, snapshot=snapshot)

    assert "Monitoring" in monitor.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if log_path.exists() and len(log_path.read_text(encoding="utf-8").splitlines()) >= 3:
            break
        time.sleep(0.01)
    assert "stopped" in monitor.stop()

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert {(event["event"], event["pid"], event["name"]) for event in events} == {
        ("started", 10, "after.exe"),
        ("started", 20, "new.exe"),
        ("stopped", 10, "before.exe"),
    }
    assert all(set(event) == {"timestamp", "event", "pid", "name"} for event in events)


def test_open_requires_confirmation_before_calling_guarded_helper(monkeypatch):
    monkeypatch.setattr(demoi, "confirm_action", lambda _description: False)

    with patch.object(demoi.foog, "open_authorized_application") as open_application:
        assert demoi.dispatch_command("open notepad", demoi.ProcessMonitor()) is True

    open_application.assert_not_called()


def test_confirmed_open_uses_existing_guarded_application_helper(monkeypatch):
    monkeypatch.setattr(demoi, "confirm_action", lambda _description: True)

    with patch.object(
        demoi.foog, "open_authorized_application", return_value="Opened notepad."
    ) as open_application:
        assert demoi.dispatch_command("open notepad", demoi.ProcessMonitor()) is True

    open_application.assert_called_once_with("notepad yes")


def test_install_requires_exact_package_and_separate_confirmation(monkeypatch):
    monkeypatch.setattr(demoi.shutil, "which", lambda _name: r"C:\Tools\winget.exe")
    monkeypatch.setattr(demoi, "confirm_action", lambda _description: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "Publisher.App")

    with patch.object(demoi.subprocess, "run") as run:
        run.return_value.returncode = 0
        demoi.install_application("Publisher App")

    assert run.call_count == 3
    assert run.call_args_list[0].args[0] == [
        r"C:\Tools\winget.exe",
        "search",
        "--query",
        "Publisher App",
        "--source",
        "winget",
    ]
    assert run.call_args_list[1].args[0] == [
        r"C:\Tools\winget.exe",
        "show",
        "--id",
        "Publisher.App",
        "--exact",
        "--source",
        "winget",
    ]
    assert run.call_args_list[2].args[0] == [
        r"C:\Tools\winget.exe",
        "install",
        "--id",
        "Publisher.App",
        "--exact",
        "--source",
        "winget",
        "--interactive",
    ]


def test_install_cancels_without_second_yes(monkeypatch):
    monkeypatch.setattr(demoi.shutil, "which", lambda _name: r"C:\Tools\winget.exe")
    confirmations = iter([True, False])
    monkeypatch.setattr(demoi, "confirm_action", lambda _description: next(confirmations))
    monkeypatch.setattr("builtins.input", lambda _prompt: "Publisher.App")

    with patch.object(demoi.subprocess, "run") as run:
        run.return_value.returncode = 0
        demoi.install_application("Publisher App")

    assert run.call_count == 2
