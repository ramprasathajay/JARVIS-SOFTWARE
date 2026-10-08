import builtins
import queue
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import apps_connect
from browser_control import BrowserController
import foog
from desktop_control import ForegroundAppController, requires_explicit_confirmation
from excel_task import apply_cell_assignments, parse_cell_assignments
from project_monitor import project_update, resolve_new_file, scan_project


def test_confirm_action_accepts_yes_case_insensitive(monkeypatch):
    for response in ("YES", "yes", "ok"):
        monkeypatch.setattr(builtins, "input", lambda response=response: response)
        assert foog.confirm_action("open app") is True


def test_project_monitor_counts_documents_and_detects_file_changes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    code_file = project / "main.py"
    code_file.write_text("print('old')", encoding="utf-8")
    (project / "notes.md").write_text("notes", encoding="utf-8")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")
    before = scan_project(project)
    assert len(before) == 2
    assert project_update(project, before, initial=True)["documents"] == 1

    code_file.write_text("print('updated code')", encoding="utf-8")
    (project / "new.txt").write_text("new document", encoding="utf-8")
    (project / "notes.md").unlink()
    after = scan_project(project)
    update = project_update(project, after, before)
    assert update["files"] == 2
    assert update["documents"] == 1
    assert update["created"] == ["new.txt"]
    assert update["deleted"] == ["notes.md"]
    assert update["modified"] == ["main.py"]


def test_generated_file_path_stays_inside_project_and_never_overwrites(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    assert resolve_new_file(project, "src/new_file.py") == project / "src" / "new_file.py"
    with TestCase().assertRaises(ValueError):
        resolve_new_file(project, "../outside.py")
    (project / "existing.py").write_text("keep", encoding="utf-8")
    with TestCase().assertRaises(FileExistsError):
        resolve_new_file(project, "existing.py")


def test_apps_connect_writes_only_confirmed_workspace_text_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert apps_connect.write_workspace_file(
        workspace, "src/main.py", "print('first')\n"
    ) == workspace / "src" / "main.py"
    with TestCase().assertRaises(FileExistsError):
        apps_connect.write_workspace_file(workspace, "src/main.py", "print('replace')\n")
    apps_connect.write_workspace_file(
        workspace, "src/main.py", "print('updated')\n", overwrite=True
    )
    assert (workspace / "src/main.py").read_text(encoding="utf-8") == "print('updated')\n"
    with TestCase().assertRaises(FileExistsError):
        apps_connect.write_workspace_file(
            workspace,
            "src/main.py",
            "print('stale preview')\n",
            overwrite=True,
            expected_existing_content="print('first')\n",
        )
    assert (workspace / "src" / "main.py").read_text(encoding="utf-8") == "print('updated')\n"
    for unsafe_path in ("../outside.py", ".env", "credentials.py", "image.exe", "CON.py"):
        with TestCase().assertRaises(ValueError):
            apps_connect.resolve_workspace_file(workspace, unsafe_path)
    assert apps_connect.resolve_workspace_file(workspace, ".gitignore") == workspace / ".gitignore"


def test_apps_connect_opens_selected_workspace_in_vscode(tmp_path):
    with patch("apps_connect._vscode_executable", return_value=tmp_path / "Code.exe"), \
         patch("apps_connect.subprocess.Popen") as popen:
        result = apps_connect.open_vscode(tmp_path)
    assert "in VS Code" in result
    popen.assert_called_once_with(
        [str(tmp_path / "Code.exe"), str(tmp_path.resolve())],
        shell=False,
    )


def test_apps_connect_installs_only_exact_recognized_winget_package():
    with patch.object(apps_connect, "is_project_tool_installed", return_value=False), \
         patch("apps_connect.shutil.which", return_value=r"C:\Windows\winget.exe"), \
         patch(
             "apps_connect.subprocess.run",
             return_value=SimpleNamespace(returncode=0, stdout="installed", stderr=""),
         ) as run:
        result = apps_connect.install_project_tool("vscode")
    assert "WinGet completed installation" in result
    command = run.call_args.args[0]
    assert command[:5] == [
        r"C:\Windows\winget.exe", "install", "--id", "Microsoft.VisualStudioCode", "--exact"
    ]
    assert run.call_args.kwargs["shell"] is False
    assert run.call_args.kwargs["timeout"] == 600

    with TestCase().assertRaises(ValueError):
        apps_connect.install_project_tool("unknown")


def test_project_checks_detect_and_report_python_syntax_failures(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("print('ok')\n", encoding="utf-8")
    assert apps_connect.available_project_checks(workspace) == ["python-syntax"]
    result = apps_connect.run_project_check(workspace, "python-syntax")
    assert result["ok"] is True
    assert result["failed"] is False
    assert "No project code was executed" in result["message"]

    (workspace / "main.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    result = apps_connect.run_project_check(workspace, "python-syntax")
    assert result["ok"] is False
    assert result["failed"] is True
    assert "main.py:1:" in result["message"]
    assert "invalid syntax" in result["message"]


def test_python_test_check_uses_fixed_command_and_workspace_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_app.py").write_text("import unittest\n", encoding="utf-8")
    assert "python-tests" in apps_connect.available_project_checks(workspace)
    with patch(
        "apps_connect.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout="FAILED (errors=1)", stderr="failure"),
    ) as run:
        result = apps_connect.run_project_check(workspace, "python-tests")
    assert result["failed"] is True
    assert result["ok"] is False
    assert "FAILED (errors=1)" in result["message"]
    assert run.call_args.args[0][1:] == ["-m", "unittest", "discover", "-v"]
    assert run.call_args.kwargs["cwd"] == workspace.resolve()
    assert run.call_args.kwargs["shell"] is False
    assert run.call_args.kwargs["timeout"] == apps_connect.CHECK_TIMEOUT_SECONDS


def test_javascript_syntax_check_uses_node_without_executing_project_code(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "app.js"
    source.write_text("console.log('hello');\n", encoding="utf-8")
    monkeypatch.setattr(
        apps_connect.shutil,
        "which",
        lambda name: r"C:\Tools\node.exe" if name == "node.exe" else None,
    )

    assert "javascript-syntax" in apps_connect.available_project_checks(workspace)
    with patch(
        "apps_connect.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
    ) as run:
        result = apps_connect.run_project_check(workspace, "javascript-syntax")

    assert result["ok"] is True
    assert "No project code was executed" in result["message"]
    assert run.call_args.args[0] == [r"C:\Tools\node.exe", "--check", "app.js"]
    assert run.call_args.kwargs["cwd"] == workspace.resolve()
    assert run.call_args.kwargs["shell"] is False

    with patch(
        "apps_connect.subprocess.run",
        return_value=SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="app.js:1\nSyntaxError: Unexpected token",
        ),
    ):
        result = apps_connect.run_project_check(workspace, "javascript-syntax")
    assert result["failed"] is True
    assert "Unexpected token" in result["message"]


def test_auto_project_check_prefers_static_syntax_over_tests(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_app.py").write_text("import unittest\n", encoding="utf-8")

    app = object.__new__(__import__("jarvis_tkinter").JarvisDesktop)
    app.project_root = workspace
    app.root = object()
    with patch("jarvis_tkinter.messagebox.askyesno", return_value=True), patch(
        "jarvis_tkinter.apps_connect.run_project_check",
        return_value={"ok": True, "failed": False, "message": "syntax passed"},
    ) as run:
        job = app._prepare_model_tool_job("run_project_check", {"check": "auto"})
        result = job()

    assert result["message"] == "syntax passed"
    run.assert_called_once_with(workspace, "python-syntax")


def test_python_check_failure_is_reported_before_fix_write_is_allowed(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent / "ai-assitant"))
    import jarvis_tkinter

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = jarvis_tkinter.JarvisDesktop.__new__(jarvis_tkinter.JarvisDesktop)
    app.project_root = workspace
    app.project_preflight_complete = True
    app.project_tools_ready = {"python", "vscode"}
    app.project_check_needs_fix = True
    app.project_fix_authorized = False
    app.root = object()
    with TestCase().assertRaisesRegex(ValueError, "wait for the user to ask"):
        app._prepare_model_tool_job(
            "write_project_file",
            {"path": "main.py", "content": "print('fixed')\n"},
        )

    app.project_fix_authorized = True
    app._preview_workspace_file = lambda _path, content, overwrite: content
    with patch("apps_connect.open_vscode_file"):
        job = app._prepare_model_tool_job(
            "write_project_file",
            {"path": "main.py", "content": "print('fixed')\n"},
        )
        result = job()
    assert "Saved main.py" in result
    assert (workspace / "main.py").read_text(encoding="utf-8") == "print('fixed')\n"
    assert app.project_check_needs_fix is False


def test_project_check_requires_confirmation_and_blocks_same_turn_fix(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent / "ai-assitant"))
    import jarvis_tkinter

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_app.py").write_text("import unittest\n", encoding="utf-8")
    app = jarvis_tkinter.JarvisDesktop.__new__(jarvis_tkinter.JarvisDesktop)
    app.project_root = workspace
    app.project_check_needs_fix = False
    app.project_fix_authorized = False
    app.root = object()

    with patch.object(jarvis_tkinter.messagebox, "askyesno", return_value=False), \
         patch.object(jarvis_tkinter.apps_connect, "run_project_check") as run:
        assert app._prepare_model_tool_job(
            "run_project_check", {"check": "python-tests"}
        ) is None
        run.assert_not_called()

    with patch.object(jarvis_tkinter.messagebox, "askyesno", return_value=True), \
         patch.object(
             jarvis_tkinter.apps_connect,
             "run_project_check",
             return_value={"ok": False, "failed": True, "message": "1 test failed"},
         ) as run:
        job = app._prepare_model_tool_job(
            "run_project_check", {"check": "python-tests"}
        )
        result = job()
    assert result["ok"] is False
    assert "1 test failed" in result["message"]
    assert app.project_check_needs_fix is True
    run.assert_called_once_with(workspace, "python-tests")

    app.project_root = workspace
    app.project_check_needs_fix = True
    app.project_fix_authorized = False
    with patch.object(jarvis_tkinter.messagebox, "askyesno", return_value=False):
        assert app._prepare_model_tool_job(
            "read_project_file", {"path": "test_app.py"}
        ) is None
    assert app.project_fix_authorized is False

    with patch.object(jarvis_tkinter.messagebox, "askyesno", return_value=True):
        read_job = app._prepare_model_tool_job(
            "read_project_file", {"path": "test_app.py"}
        )
    assert "Source file: test_app.py" in read_job()
    assert app.project_fix_authorized is True


def test_terminal_voice_and_text_actions_preserve_command_case_and_quotes():
    assert foog.parse_voice_action('run python -c "print(\'Hello World\')"') == (
        "terminal", 'python -c "print(\'Hello World\')"'
    )
    assert foog.parse_voice_action("/terminal Git status") == ("terminal", "Git status")


def test_terminal_runner_uses_gui_confirmation_callback(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")

    with patch("foog.subprocess.run") as run:
        run.return_value.stdout = "ok"
        run.return_value.stderr = ""
        run.return_value.returncode = 0
        result = foog.run_authorized_command(
            "python --version", confirmation_callback=lambda _description: True
        )

    assert "Exit code: 0" in result
    run.assert_called_once()


def test_terminal_permission_remembers_only_exact_command(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    monkeypatch.setattr(foog, "COMMAND_PERMISSION_PATH", tmp_path / "approved.json")
    monkeypatch.setattr(foog, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")

    with patch("foog.subprocess.run") as run:
        run.return_value.stdout = "ok"
        run.return_value.stderr = ""
        run.return_value.returncode = 0
        result = foog.run_authorized_command(
            "python --version", confirmation_callback=lambda _description: "always"
        )
        assert "Exit code: 0" in result
        assert foog.is_command_persistently_approved("python --version")
        assert not foog.is_command_persistently_approved("python -V")
        foog.run_authorized_command(
            "python --version",
            confirmation_callback=lambda _description: (_ for _ in ()).throw(
                AssertionError("saved permission should not prompt")
            ),
        )

    assert run.call_count == 2


def test_model_not_found_is_recognized_for_fallback():
    error = RuntimeError(
        "Error code: 404 - {'code': 'model_not_found', "
        "'message': 'The model groq-compound-v1 does not exist'}"
    )
    assert foog.is_model_permission_error(error) is True


def test_default_models_use_current_provider_ids():
    assert foog.DEFAULT_GROQ_MODEL == "openai/gpt-oss-120b"
    assert foog.DEFAULT_GOOGLE_MODEL == "gemini-3.8-flash"


def test_open_and_system_commands_accept_yes_flag(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")

    with patch("foog.confirm_action", return_value=True), patch("foog.webbrowser.open") as browser_open:
        result = foog.open_authorized_application("browser https://example.com YES")
        assert "Opened https://example.com" in result
        browser_open.assert_called_once_with("https://example.com", new=2)

    with patch("foog.confirm_action", return_value=True), patch("foog.subprocess.Popen") as popen:
        result = foog.control_authorized_system("shutdown ok")
        assert "Requested shutdown." == result
        popen.assert_called_once()


def test_social_web_apps_open_official_sites(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")

    with patch("foog.webbrowser.open") as browser_open:
        assert "web.whatsapp.com" in foog.open_authorized_application("whatsapp yes")
        assert "www.instagram.com" in foog.open_authorized_application("instagram YES")

    assert [call.args[0] for call in browser_open.call_args_list] == [
        "https://web.whatsapp.com/",
        "https://www.instagram.com/",
    ]


def test_open_and_system_commands_require_confirmation(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")

    with patch("foog.webbrowser.open") as browser_open:
        assert "Confirmation required" in foog.open_authorized_application("browser https://example.com")
        browser_open.assert_not_called()

    with patch("foog.subprocess.Popen") as popen:
        assert "Confirmation required" in foog.control_authorized_system("lock")
        popen.assert_not_called()


def test_voice_app_and_system_actions_accept_optional_yes():
    assert foog.parse_voice_action("open") == ("open_app_prompt", "")
    assert foog.parse_voice_action("open notepad") == ("open", "notepad yes")
    assert foog.parse_voice_action("open app") == ("open_app_prompt", "")
    assert foog.parse_voice_action("open app notepad") == ("open", "notepad yes")
    assert foog.parse_voice_action("launch application editor") == ("open", "editor yes")
    assert foog.parse_voice_action("close notepad YES") == ("close", "notepad yes")
    assert foog.parse_voice_action("close notepad ok") == ("close", "notepad yes")
    assert foog.parse_voice_action("close notepad") == ("close", "notepad yes")
    assert foog.parse_voice_action("shutdown computer YES") == ("system", "shutdown yes")
    assert foog.parse_voice_action("shutdown computer ok") == ("system", "shutdown yes")
    assert foog.parse_voice_action("shutdown computer") == ("system", "shutdown yes")


def test_voice_code_generation_preserves_filename_language_and_concept():
    assert foog.parse_voice_action(
        "create new file src/demo.py using Python about print green dots"
    ) == ("generate_code", "src/demo.py | Python | print green dots")


def test_desktop_install_and_destructive_clicks_need_explicit_confirmation():
    assert requires_explicit_confirmation("click Install")
    assert requires_explicit_confirmation("click Delete file")
    assert not requires_explicit_confirmation("click Search")
    assert not requires_explicit_confirmation("scroll down")


def test_screen_commands_parse_without_changing_browser_commands():
    assert foog.parse_voice_action("screen scroll down") == (
        "desktop_control", "scroll down"
    )
    assert foog.parse_voice_action("desktop search for VLC") == (
        "desktop_control", "search VLC"
    )
    assert foog.parse_voice_action("screen tap Install") == (
        "desktop_control", "click Install"
    )
    assert foog.parse_voice_action("screen status") == ("desktop_control", "status")
    assert foog.parse_voice_action("scroll up") == (
        "browser_control", "scroll up yes"
    )


def test_desktop_controller_keeps_master_lock_and_install_confirmation(monkeypatch):
    controller = ForegroundAppController()
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "0")
    assert "locked" in controller.execute("status").lower()

    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    assert "separate confirmation" in controller.execute("click Install").lower()


def test_desktop_controller_targets_accessible_controls(monkeypatch):
    from unittest.mock import Mock

    from desktop_control import ForegroundAppController

    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    controller = ForegroundAppController()
    search = Mock()
    search.element_info = SimpleNamespace(name="Search", control_type="Edit")
    search.is_visible.return_value = True
    search.is_enabled.return_value = True
    install = Mock()
    install.element_info = SimpleNamespace(name="Install", control_type="Button")
    install.is_visible.return_value = True
    install.is_enabled.return_value = True
    window = Mock()
    window.descendants.side_effect = lambda control_type=None: (
        [search] if control_type == "Edit" else [search, install]
    )
    controller._target_window = lambda: ("Microsoft Store", window)

    assert "scrolled down" in controller.execute("scroll down").lower()
    window.wheel_mouse_input.assert_called_once_with(wheel_dist=-3)
    assert "Searched Microsoft Store" in controller.execute("search VLC")
    search.click_input.assert_called_once()
    assert search.type_keys.call_count == 3
    assert "separate confirmation" in controller.execute("click Install")
    assert "Clicked Button" in controller.execute("click Install", confirmed=True)
    install.click_input.assert_called_once()


def test_desktop_click_launches_unique_installed_app_when_control_is_not_visible(monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock

    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    controller = ForegroundAppController()
    window = Mock()
    window.descendants.return_value = []
    controller._target_window = lambda expected_handle=None: ("Desktop", window)
    shortcut = Path(r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Sample App.lnk")
    controller._find_installed_app_shortcuts = lambda _label: [shortcut]

    with patch("desktop_control.os.startfile") as startfile:
        result = controller.execute("click Sample App")

    assert result == "Opened installed app 'Sample App' from the Start Menu."
    startfile.assert_called_once_with(str(shortcut))


def test_desktop_click_does_not_guess_between_installed_apps(monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock

    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    controller = ForegroundAppController()
    window = Mock()
    window.descendants.return_value = []
    controller._target_window = lambda expected_handle=None: ("Desktop", window)
    controller._find_installed_app_shortcuts = lambda _label: [
        Path(r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Sample App.lnk"),
        Path(r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Sample App Tools.lnk"),
    ]

    with patch("desktop_control.os.startfile") as startfile:
        result = controller.execute("click Sample App")

    assert "Several installed apps match" in result
    startfile.assert_not_called()


def test_installed_app_search_checks_user_and_system_start_menus(monkeypatch, tmp_path):
    from desktop_control import ForegroundAppController

    user_start_menu = tmp_path / "user" / "Programs"
    system_start_menu = tmp_path / "system" / "Programs"
    user_shortcut = user_start_menu / "Browser" / "Sample Browser.lnk"
    system_shortcut = system_start_menu / "Utilities" / "Sample Browser Tools.lnk"
    user_shortcut.parent.mkdir(parents=True)
    system_shortcut.parent.mkdir(parents=True)
    user_shortcut.touch()
    system_shortcut.touch()
    monkeypatch.setenv("APPDATA", str(tmp_path / "user"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "system"))

    matches = ForegroundAppController._find_installed_app_shortcuts("sample browser")

    assert matches == [user_shortcut]
    assert set(ForegroundAppController._find_installed_app_shortcuts("browser")) == {
        user_shortcut,
        system_shortcut,
    }


def test_excel_cell_assignments_parse_as_literal_text():
    assert parse_cell_assignments(
        "a1=Quarterly report\nB2 equals 00123\nC3==SUM(A1:A2)"
    ) == [
        ("A1", "Quarterly report"),
        ("B2", "00123"),
        ("C3", "=SUM(A1:A2)"),
    ]


def test_excel_cell_assignments_reject_invalid_or_ambiguous_edits():
    for invalid in ("", "A1=", "A0=value", "XFE1=value", "A1048577=value", "A1=one\nA1=two"):
        with TestCase().assertRaises(ValueError):
            parse_cell_assignments(invalid)


def test_excel_task_opens_selected_workbook_safely_and_saves_cells(monkeypatch, tmp_path):
    pythoncom = ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: None
    pythoncom.CoUninitialize = lambda: None
    cells = {}

    def range_for(address):
        return cells.setdefault(address, SimpleNamespace())

    worksheet = SimpleNamespace(Name="Sheet1", Range=range_for)
    workbook = SimpleNamespace(ActiveSheet=worksheet, ReadOnly=False, Save=lambda: None)
    excel = SimpleNamespace(
        Visible=False,
        DisplayAlerts=False,
        AutomationSecurity=2,
        Workbooks=SimpleNamespace(Open=lambda *_args, **_kwargs: workbook),
    )
    client = ModuleType("win32com.client")
    client.DispatchEx = lambda _name: excel
    win32com = ModuleType("win32com")
    win32com.__path__ = []
    win32com.client = client
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    workbook_path = tmp_path / "report.xlsx"
    workbook_path.touch()

    result = apply_cell_assignments(
        workbook_path,
        parse_cell_assignments("A1=Quarterly report\nB2=00123"),
    )

    assert "Updated 2 cell(s) on 'Sheet1' and saved report.xlsx" in result
    assert cells["A1"].NumberFormat == "@"
    assert cells["A1"].Value2 == "Quarterly report"
    assert cells["B2"].Value2 == "00123"
    assert excel.Visible is True
    assert excel.DisplayAlerts is True
    assert excel.AutomationSecurity == 2


def test_browser_shutdown_timeout_is_reported_and_can_be_retried():
    controller = BrowserController()
    release_shutdown = threading.Event()

    def delayed_browser_worker():
        operation, _, response = controller._commands.get()
        assert operation == "close"
        release_shutdown.wait(timeout=1)
        response.put("Browser closed.")

    controller._thread = threading.Thread(target=delayed_browser_worker, daemon=True)
    controller._thread.start()

    with TestCase().assertRaises(TimeoutError):
        controller.close(timeout=0.01)

    release_shutdown.set()
    assert controller.close(timeout=1) == "Browser closed."
    assert controller._thread is None


def test_close_browser_returns_shutdown_result(monkeypatch):
    import browser_control

    class Controller:
        def close(self):
            return "Browser closed."

    monkeypatch.setattr(browser_control, "_controller", Controller())

    assert browser_control.close_browser() == "Browser closed."


def test_voice_store_and_named_download_actions():
    assert foog.parse_voice_action("open Microsoft Store") == ("store", "home yes")
    assert foog.parse_voice_action("install software VLC media player") == (
        "store", "search vlc media player"
    )
    assert foog.parse_voice_action("install app Visual Studio Code yes") == (
        "store", "search visual studio code yes"
    )
    assert foog.parse_voice_action("download pdf annual report") == (
        "download_search", "pdf annual report"
    )
    assert foog.parse_voice_action("download software Blender") == (
        "download_search", "software blender"
    )
    assert foog.parse_voice_action("download annual report as PDF") == (
        "download_search", "pdf annual report"
    )
    assert foog.parse_voice_action("Run Installer") == ("run_installer", "")
    assert foog.parse_voice_action("install downloaded app") == ("run_installer", "")
    assert foog.parse_voice_action("open browser") == ("open", "browser yes")


def test_open_installed_app_and_edge_explore_routes():
    assert foog.parse_voice_action("open app VLC media player") == (
        "open_installed_app",
        "vlc media player",
    )
    assert foog.parse_voice_action("open Visual Studio Code") == (
        "open_installed_app",
        "visual studio code",
    )
    assert foog.parse_voice_action("open browser") == ("open", "browser yes")
    assert foog.parse_voice_action("open browser https://example.com") == (
        "open",
        "browser https://example.com yes",
    )
    assert foog.parse_voice_action("open github") == ("browse", "github yes")
    assert foog.parse_voice_action("open app") == ("open_app_prompt", "")
    assert foog.parse_voice_action("open tab") == ("browser_control", "open_tab yes")
    assert foog.parse_voice_action("explore climate change news") == (
        "browse",
        "climate change news yes",
    )
    assert foog.parse_voice_action("explore https://example.com") == (
        "browse",
        "https://example.com yes",
    )


def test_explore_query_uses_controlled_edge_search(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    with patch("foog._public_url", side_effect=lambda url: (url, None)), patch(
        "foog.browser_control.execute_browser_action",
        return_value="Search opened in the controlled browser.",
    ) as execute_browser_action:
        result = foog.browse_authorized_url("climate change news yes")

    assert result == "Search opened in the controlled browser."
    execute_browser_action.assert_called_once_with(
        "open",
        "https://www.google.com/search?q=climate+change+news",
    )


def test_store_search_requires_confirmation_and_opens_store_results(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    with patch("foog.webbrowser.open", return_value=True) as browser_open:
        assert "Confirmation required" in foog.open_authorized_store("search vlc")
        browser_open.assert_not_called()
        result = foog.open_authorized_store("search vlc yes")

    assert "Review the publisher" in result
    browser_open.assert_called_once_with("ms-windows-store://search/?query=vlc", new=0)


def test_open_browser_without_url_uses_controlled_browser_homepage(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    with patch(
        "foog.browser_control.execute_browser_action",
        return_value="Opened the homepage.",
    ) as open_browser:
        result = foog.open_authorized_application("browser yes")
    assert result == "Opened the homepage."
    open_browser.assert_called_once_with("open", "https://www.google.com/")


def test_named_document_download_opens_pdf_search(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    with patch("foog.search_authorized_web", return_value="Opened results.") as search:
        result = foog.search_downloadable_item("pdf annual report")
    assert "direct public URL" in result
    search.assert_called_once_with("annual report filetype:pdf")


def test_voice_and_text_mode_support_staged_whole_system_access_policy():
    assert foog.parse_voice_action("enable whole system access") == ("system_access", "enable")
    assert foog.parse_voice_action("disable whole system access") == ("system_access", "disable")
    assert "locked by default" in foog.configure_system_access("enable").lower()
    assert "locked by default" in foog.configure_system_access("disable").lower()


def test_voice_web_search_and_secure_url_actions():
    assert foog.parse_voice_action("search for movie Dune") == ("search", "movie dune")
    assert foog.parse_voice_action("search browser for game Minecraft") == (
        "search", "game minecraft"
    )
    assert foog.parse_voice_action("browser search example.com") == (
        "search", "example.com"
    )
    assert foog.parse_voice_action("browser search for example.com") == (
        "search", "example.com"
    )
    assert foog.parse_voice_action("find game Minecraft") == ("search", "game minecraft")
    assert foog.parse_voice_action("files linux iso") == ("search", "files linux iso")
    assert foog.parse_voice_action("explore https://example.com/safe") == (
        "browse", "https://example.com/safe yes"
    )
    assert foog.parse_voice_action("explore secure website https://example.com/safe") == (
        "browse", "https://example.com/safe yes"
    )
    assert foog.parse_voice_action("download https://example.com/file.pdf") == (
        "download", "https://example.com/file.pdf yes"
    )
    assert foog.parse_voice_action("explore http://example.com") == (None, None)
    assert foog.parse_voice_action("search for any domain Acme tools") == (
        "search", "any domain acme tools"
    )


def test_voice_browser_control_commands():
    assert foog.parse_voice_action("open website GitHub") == ("browse", "github yes")
    assert foog.parse_voice_action("open website an unlisted company") == (
        "browse", "an unlisted company yes"
    )
    assert foog.parse_voice_action("go to YouTube") == ("browse", "youtube yes")
    assert foog.parse_voice_action("visit example.com") == ("browse", "example.com yes")
    assert foog.parse_voice_action("open YouTube") == ("browse", "youtube yes")
    assert foog.parse_voice_action("open terminal") == ("open", "terminal yes")
    assert foog.parse_voice_action("open app notepad") == ("open", "notepad yes")
    assert foog.parse_voice_action("scroll down") == ("browser_control", "scroll down yes")
    assert foog.parse_voice_action("scroll up yes") == ("browser_control", "scroll up yes")
    assert foog.parse_voice_action("click Python documentation") == (
        "browser_control", "click python documentation yes"
    )
    assert foog.parse_voice_action("tap the button Continue") == (
        "browser_control", "click continue yes"
    )
    assert foog.parse_voice_action("type Search for Dune!") == (
        "browser_control", "type Search for Dune! yes"
    )
    assert foog.parse_voice_action("go fullscreen") == ("browser_control", "fullscreen yes")
    assert foog.parse_voice_action("play") == ("browser_control", "play yes")
    assert foog.parse_voice_action("pause playback") == ("browser_control", "pause yes")
    assert foog.parse_voice_action("next tab") == ("browser_control", "next_tab yes")
    assert foog.parse_voice_action("open a new tab") == ("browser_control", "open_tab yes")
    assert foog.parse_voice_action("private tab") == ("browser_control", "open_tab yes")
    assert foog.parse_voice_action("open private tab") == ("browser_control", "open_tab yes")
    assert foog.parse_voice_action("open a private tab") == ("browser_control", "open_tab yes")
    assert foog.parse_voice_action("browser private tab") == ("browser_control", "open_tab yes")
    assert foog.parse_voice_action("zoom plus") == ("browser_control", "zoom in yes")
    assert foog.parse_voice_action("minus") == ("browser_control", "zoom out yes")


def test_browser_media_and_tab_actions_require_confirmation(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    for operation in ("play", "pause", "next_tab", "open_tab"):
        with patch("foog.browser_control.execute_browser_action", return_value="Done.") as execute:
            assert foog.control_authorized_browser(f"{operation} yes") == "Done."
        execute.assert_called_once_with(operation)

    with patch("foog.browser_control.execute_browser_action") as execute:
        assert "Confirmation required" in foog.control_authorized_browser("next_tab")
        execute.assert_not_called()


def test_browser_launches_inprivate_and_new_tabs_are_private(monkeypatch):
    launched = {}

    class FakePage:
        def is_closed(self):
            return False

    class FakeContext:
        def route(self, *_args):
            pass

        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeBrowser:
        def is_connected(self):
            return True

        def new_context(self):
            return FakeContext()

        def close(self):
            pass

    class FakeChromium:
        def launch(self, **kwargs):
            launched.update(kwargs)
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            pass

    playwright_package = ModuleType("playwright")
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: FakePlaywright())
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    controller = BrowserController()
    assert "InPrivate tab" in controller.execute("open_tab")
    assert controller.close() == "Browser closed."
    assert launched == {
        "channel": "msedge",
        "headless": False,
        "args": ["--inprivate"],
    }


def test_browser_controls_require_enabled_actions_and_confirmation(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "0")
    with patch("foog.browser_control.execute_browser_action") as execute:
        assert "disabled" in foog.control_authorized_browser("scroll down yes").lower()
        execute.assert_not_called()

    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    with patch("foog.browser_control.execute_browser_action") as execute:
        assert "Confirmation required" in foog.control_authorized_browser("scroll down")
        execute.assert_not_called()

        execute.return_value = "Scrolled down."
        assert foog.control_authorized_browser("scroll down yes") == "Scrolled down."
        execute.assert_called_once_with("scroll", "down")

    with patch("foog.browser_control.execute_browser_action", return_value="Typed.") as execute:
        assert foog.control_authorized_browser("type Hello World yes") == "Typed."
        execute.assert_called_once_with("type", "Hello World")


def test_open_named_website_uses_controlled_browser(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    monkeypatch.setattr(foog, "_public_url", lambda url: (url, None))
    with patch("foog.browser_control.execute_browser_action", return_value="Opened site.") as execute:
        assert foog.browse_authorized_url("github yes") == "Opened site."
    execute.assert_called_once_with("open", "https://github.com/")

    with patch("foog.browser_control.execute_browser_action", return_value="Opened search.") as execute:
        assert foog.browse_authorized_url("python documentation yes") == "Opened search."
    assert execute.call_args.args == (
        "open", "https://www.google.com/search?q=python+documentation"
    )

    monkeypatch.setattr(foog, "_public_url", lambda url: (url, None))
    with patch("foog.browser_control.execute_browser_action", return_value="Opened site.") as execute:
        assert foog.browse_authorized_url("portal.example yes") == "Opened site."
    execute.assert_called_once_with("open", "https://portal.example")


def test_browser_search_requires_commands_enabled(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "0")

    with patch("foog.browser_control.execute_browser_action") as open_browser:
        assert "disabled" in foog.search_authorized_web("movie dune").lower()
        open_browser.assert_not_called()


def test_browser_search_opens_results_in_the_controlled_browser(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")

    with patch(
        "foog.browser_control.execute_browser_action",
        return_value="Search results opened.",
    ) as open_browser:
        assert foog.search_authorized_web("movie dune") == "Search results opened."

    open_browser.assert_called_once_with(
        "open", "https://www.google.com/search?q=movie+dune"
    )


def test_disable_commands_overrides_enabled_setting(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    monkeypatch.setenv("JARVIS_DISABLE_COMMANDS", "1")

    with patch("foog.subprocess.run") as run:
        result = foog.run_authorized_command("python --version")

    assert "JARVIS_DISABLE_COMMANDS" in result
    run.assert_not_called()
    assert foog.command_execution_enabled() is False


def test_write_command_does_not_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    monkeypatch.setattr(foog, "WORKSPACE_ROOT", tmp_path)

    with patch("foog.confirm_action", side_effect=AssertionError("confirmation was requested")):
        with patch("foog.subprocess.Popen"):
            result = foog.write_authorized_content("note.txt | hello")

    assert result == "Wrote note.txt and opened it in notepad."
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello"


def test_close_command_uses_allowlisted_process(monkeypatch):
    monkeypatch.setenv("JARVIS_ENABLE_COMMANDS", "1")
    monkeypatch.setattr(foog.os, "name", "nt")

    with patch("foog.subprocess.run") as run:
        run.return_value.returncode = 0
        assert foog.close_authorized_application("notepad ok") == "Closed notepad."
        run.assert_called_once_with(
            ["taskkill", "/IM", "notepad.exe", "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )


def test_plugin_controls_show_safe_mcp_and_connector_status():
    plugin_text = foog.list_plugin_controls()
    assert "Available system plugins:" in plugin_text
    assert "MCP / third-party connectors:" in plugin_text
    assert "No external MCP servers are configured" in plugin_text
    assert "not auto-connected" in plugin_text.lower()
