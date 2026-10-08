import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


PROJECT_TOOLS = {
	"vscode": {
		"label": "Visual Studio Code",
		"package_id": "Microsoft.VisualStudioCode",
		"commands": ("code.exe",),
	},
	"python": {
		"label": "Python",
		"package_id": "Python.Python.3.14",
		"commands": ("python.exe", "py.exe"),
	},
	"nodejs": {
		"label": "Node.js LTS",
		"package_id": "OpenJS.NodeJS.LTS",
		"commands": ("node.exe", "npm.cmd"),
	},
	"git": {
		"label": "Git",
		"package_id": "Git.Git",
		"commands": ("git.exe",),
	},
	"dotnet": {
		"label": ".NET SDK",
		"package_id": "Microsoft.DotNet.SDK.9",
		"commands": ("dotnet.exe",),
	},
	"java": {
		"label": "Java JDK",
		"package_id": "EclipseAdoptium.Temurin.21.JDK",
		"commands": ("javac.exe",),
	},
	"go": {
		"label": "Go",
		"package_id": "GoLang.Go",
		"commands": ("go.exe",),
	},
	"rust": {
		"label": "Rust",
		"package_id": "Rustlang.Rust.MSVC",
		"commands": ("rustc.exe", "cargo.exe"),
	},
	"blender": {
		"label": "Blender",
		"package_id": "BlenderFoundation.Blender",
		"commands": ("blender.exe",),
	},
	"godot": {
		"label": "Godot",
		"package_id": "GodotEngine.GodotEngine",
		"commands": ("godot.exe", "godot"),
	},
}
PROJECT_TOOL_ALIASES = {
	"c#": "dotnet",
	".net": "dotnet",
	"dotnet sdk": "dotnet",
	"javascript": "nodejs",
	"node": "nodejs",
	"node.js": "nodejs",
	"python 3": "python",
	"python3": "python",
	"rustlang": "rust",
	"typescript": "nodejs",
	"visual studio code": "vscode",
	"vs code": "vscode",
}
TEXT_FILE_EXTENSIONS = {
	".c", ".cpp", ".cs", ".css", ".gd", ".go", ".h", ".html", ".java",
	".js", ".jsx", ".json", ".md", ".py", ".rs", ".toml", ".ts", ".tsx",
	".txt", ".vue", ".xml", ".yaml", ".yml",
}
EXTENSIONLESS_TEXT_FILES = {
	".dockerignore", ".editorconfig", ".gitignore", "Dockerfile", "Makefile",
}
BLOCKED_FILE_NAMES = {".env", ".git", "credentials", "secrets"}
WINDOWS_RESERVED_NAMES = {
	"CON", "PRN", "AUX", "NUL",
	*(f"COM{index}" for index in range(1, 10)),
	*(f"LPT{index}" for index in range(1, 10)),
}
MAX_FILE_BYTES = 100_000
MAX_CHECK_OUTPUT_CHARS = 12_000
CHECK_TIMEOUT_SECONDS = 120
IGNORED_PROJECT_DIRECTORIES = {
	".git", ".idea", ".pytest_cache", ".venv", "__pycache__", "build",
	"dist", "node_modules", "venv",
}


def _project_files(workspace, suffixes):
	root = Path(workspace).resolve()
	if not root.is_dir():
		raise NotADirectoryError(f"Workspace folder does not exist: {root}")
	files = []
	for current_root, directories, filenames in os.walk(root, followlinks=False):
		directories[:] = [
			name for name in directories
			if name.casefold() not in IGNORED_PROJECT_DIRECTORIES
			and not (Path(current_root) / name).is_symlink()
		]
		for filename in filenames:
			path = Path(current_root) / filename
			if path.suffix.casefold() in suffixes and not path.is_symlink():
				files.append(path)
				if len(files) > 500:
					raise ValueError("Project check stopped: workspace contains more than 500 relevant files.")
	return files


def available_project_checks(workspace):
	root = Path(workspace).resolve()
	checks = []
	python_files = _project_files(root, {".py"})
	if python_files:
		checks.append("python-syntax")
		if any(
			path.name.casefold().startswith("test_")
			or path.name.casefold().endswith("_test.py")
			or "tests" in {part.casefold() for part in path.relative_to(root).parts[:-1]}
			for path in python_files
		):
			checks.append("python-tests")
	javascript_files = _project_files(root, {".js", ".mjs", ".cjs"})
	if javascript_files and (shutil.which("node.exe") or shutil.which("node")):
		checks.append("javascript-syntax")
	if _project_files(root, {".sln", ".slnx", ".csproj"}):
		if shutil.which("dotnet.exe") or shutil.which("dotnet"):
			checks.append("dotnet-tests")
	return checks


def _truncate_check_output(output):
	if len(output) <= MAX_CHECK_OUTPUT_CHARS:
		return output
	return output[:MAX_CHECK_OUTPUT_CHARS] + "\n...[output truncated]"


def project_check_command(workspace, check):
	root = Path(workspace).resolve()
	if check == "javascript-syntax":
		node = shutil.which("node.exe") or shutil.which("node")
		if node is None:
			raise FileNotFoundError("Node.js is not available.")
		javascript_files = _project_files(root, {".js", ".mjs", ".cjs"})
		if not javascript_files:
			raise FileNotFoundError("No JavaScript source files were found.")
		return [node, "--check", str(javascript_files[0].relative_to(root))]
	if check == "python-tests":
		return [sys.executable, "-m", "unittest", "discover", "-v"]
	if check == "dotnet-tests":
		dotnet = shutil.which("dotnet.exe") or shutil.which("dotnet")
		if dotnet is None:
			raise FileNotFoundError(".NET SDK is not available.")
		projects = sorted(
			_project_files(root, {".sln", ".slnx", ".csproj"}),
			key=lambda path: (0 if path.suffix.casefold() in {".sln", ".slnx"} else 1, str(path).casefold()),
		)
		if not projects:
			raise FileNotFoundError("No .NET solution or project file was found.")
		return [
			dotnet, "test", str(projects[0].relative_to(root)),
			"--no-restore", "--verbosity", "minimal",
		]
	raise ValueError(f"No process command is defined for project check {check!r}.")


def describe_project_check(workspace, check):
	if check in {"python-syntax", "javascript-syntax"}:
		language = "Python" if check == "python-syntax" else "JavaScript"
		return f"Static {language} syntax check (does not execute project code)"
	return subprocess.list2cmdline(project_check_command(workspace, check))


def run_project_check(workspace, check):
	root = Path(workspace).resolve()
	checks = available_project_checks(root)
	if check == "auto":
		check = next(
			(name for name in ("python-syntax", "javascript-syntax") if name in checks),
			None,
		)
	if check not in checks:
		available = ", ".join(checks) or "none detected"
		return {
			"ok": False,
			"failed": False,
			"message": f"Cannot run project check {check!r}. Available checks: {available}.",
		}

	if check in {"python-syntax", "javascript-syntax"}:
		if check == "python-syntax":
			source_files = _project_files(root, {".py"})
			language = "Python"
		else:
			node = shutil.which("node.exe") or shutil.which("node")
			if node is None:
				return {
					"ok": False,
					"failed": False,
					"message": "Node.js is not available for the JavaScript syntax check.",
				}
			source_files = _project_files(root, {".js", ".mjs", ".cjs"})
			language = "JavaScript"
		diagnostics = []
		for path in source_files:
			if check == "javascript-syntax":
				command = [node, "--check", str(path.relative_to(root))]
				try:
					result = subprocess.run(
						command,
						cwd=root,
						capture_output=True,
						text=True,
						timeout=CHECK_TIMEOUT_SECONDS,
						check=False,
						shell=False,
					)
				except subprocess.TimeoutExpired:
					diagnostics.append(
						f"{path.relative_to(root)}: syntax check timed out after "
						f"{CHECK_TIMEOUT_SECONDS} seconds"
					)
					continue
				except OSError as exc:
					diagnostics.append(
						f"{path.relative_to(root)}: could not start Node.js syntax check: {exc}"
					)
					continue
				if result.returncode:
					output = "\n".join(
						part.strip() for part in (result.stdout, result.stderr) if part.strip()
					)
					diagnostics.append(
						f"{path.relative_to(root)}: "
						+ _truncate_check_output(output or "syntax check failed")
					)
				continue
			try:
				source = path.read_text(encoding="utf-8")
			except (OSError, UnicodeError) as exc:
				diagnostics.append(f"{path.relative_to(root)}: could not read UTF-8 source: {exc}")
				continue
			try:
				compile(source, str(path), "exec")
			except SyntaxError as exc:
				diagnostics.append(
					f"{path.relative_to(root)}:{exc.lineno or 1}:{exc.offset or 1}: {exc.msg}"
				)
		if diagnostics:
			return {
				"ok": False,
				"failed": True,
				"message": f"{language} syntax check found issues:\n" + "\n".join(diagnostics[:100]),
			}
		return {
			"ok": True,
			"failed": False,
			"message": (
				f"{language} syntax check passed for {len(source_files)} file(s). "
				"No project code was executed."
			),
		}

	try:
		command = project_check_command(root, check)
	except (FileNotFoundError, ValueError) as exc:
		return {"ok": False, "failed": False, "message": str(exc)}

	try:
		result = subprocess.run(
			command,
			cwd=root,
			capture_output=True,
			text=True,
			timeout=CHECK_TIMEOUT_SECONDS,
			check=False,
			shell=False,
		)
	except subprocess.TimeoutExpired:
		return {
			"ok": False,
			"failed": True,
			"message": f"Project check timed out after {CHECK_TIMEOUT_SECONDS} seconds: {' '.join(command)}",
		}
	except OSError as exc:
		return {
			"ok": False,
			"failed": False,
			"message": f"Could not start the approved project check: {exc}",
		}

	output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
	if result.returncode == 0:
		return {
			"ok": True,
			"failed": False,
			"message": (
				f"Project check passed (exit code 0): {' '.join(command)}\n"
				+ (_truncate_check_output(output) or "[no output]")
			),
		}
	return {
		"ok": False,
		"failed": True,
		"message": (
			f"Project check failed (exit code {result.returncode}): {' '.join(command)}\n"
			+ (_truncate_check_output(output) or "[no output]")
		),
	}


def read_workspace_file(workspace, relative_path):
	target = resolve_workspace_file(workspace, relative_path)
	try:
		content = target.read_text(encoding="utf-8")
	except (OSError, UnicodeError) as exc:
		raise ValueError(f"Could not read this file as UTF-8 text: {exc}") from exc
	if len(content.encode("utf-8")) > MAX_FILE_BYTES:
		raise ValueError(f"Project files shared with the AI are limited to {MAX_FILE_BYTES} UTF-8 bytes.")
	return content


def resolve_workspace_file(workspace, relative_path):
	workspace_path = Path(workspace).resolve()
	requested = Path(relative_path)
	if not workspace_path.is_dir():
		raise NotADirectoryError(f"Workspace folder does not exist: {workspace_path}")
	if not relative_path or requested.is_absolute() or requested.drive or requested.root:
		raise ValueError("Choose a relative file path inside the selected workspace.")

	parts = relative_path.replace("\\", "/").split("/")
	if (
		len(relative_path) > 220
		or any(
			not part or part in {".", ".."}
			or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
			or part.upper().split(".")[0] in WINDOWS_RESERVED_NAMES
			for part in parts
		)
		or any(
			part.casefold() in BLOCKED_FILE_NAMES
			or Path(part).stem.casefold() in BLOCKED_FILE_NAMES
			or part.casefold().startswith(".env.")
			for part in parts
		)
		or (
			Path(parts[-1]).suffix.casefold() not in TEXT_FILE_EXTENSIONS
			and parts[-1] not in EXTENSIONLESS_TEXT_FILES
		)
	):
		raise ValueError("That file path or extension is not allowed for generated text files.")

	joined_target = workspace_path / Path(*parts)
	if joined_target.is_symlink():
		raise ValueError("Generated files cannot replace symbolic links.")
	target = joined_target.resolve()
	try:
		target.relative_to(workspace_path)
	except ValueError as exc:
		raise ValueError("The file must stay inside the selected workspace.") from exc

	current = workspace_path
	for part in parts[:-1]:
		current = current / part
		if current.is_symlink():
			raise ValueError("Generated files cannot be written through symbolic-link folders.")
	if target.exists() and not target.is_file():
		raise ValueError("The selected file path is not a regular file.")
	return target


def validate_workspace_content(content):
	if not isinstance(content, str) or not content.strip():
		raise ValueError("Generated file content must be non-empty text.")
	if len(content.encode("utf-8")) > MAX_FILE_BYTES:
		raise ValueError(f"Generated files are limited to {MAX_FILE_BYTES} UTF-8 bytes.")
	return content


def required_tools_for_file(relative_path):
	extension = Path(relative_path).suffix.casefold()
	return {
		".py": ("python",),
		".js": ("nodejs",),
		".jsx": ("nodejs",),
		".ts": ("nodejs",),
		".tsx": ("nodejs",),
		".cs": ("dotnet",),
		".java": ("java",),
		".go": ("go",),
		".rs": ("rust",),
	}.get(extension, ())


def write_workspace_file(
	workspace,
	relative_path,
	content,
	overwrite=False,
	expected_existing_content=None,
):
	validate_workspace_content(content)
	target = resolve_workspace_file(workspace, relative_path)
	if target.exists() and not overwrite:
		raise FileExistsError(target)
	if overwrite and expected_existing_content is not None:
		try:
			current_content = target.read_text(encoding="utf-8")
		except (OSError, UnicodeError) as exc:
			raise FileExistsError("The existing file changed or became unreadable after preview.") from exc
		if current_content != expected_existing_content:
			raise FileExistsError("The existing file changed after preview; review it again before replacing.")
	target.parent.mkdir(parents=True, exist_ok=True)

	if not overwrite:
		with target.open("x", encoding="utf-8", newline="") as output:
			output.write(content)
		return target

	with tempfile.NamedTemporaryFile(
		mode="w",
		encoding="utf-8",
		newline="",
		dir=target.parent,
		prefix=f".{target.name}.",
		suffix=".tmp",
		delete=False,
	) as output:
		temporary_path = Path(output.name)
		output.write(content)
	try:
		os.replace(temporary_path, target)
	except OSError:
		temporary_path.unlink(missing_ok=True)
		raise
	return target


def _vscode_executable():
	candidates = []
	configured_editor = os.getenv("JARVIS_CODE_EDITOR", "").strip()
	if configured_editor:
		configured_path = Path(configured_editor).expanduser()
		if configured_path.is_file() and configured_path.suffix.casefold() == ".exe":
			candidates.append(configured_path)

	path_editor = shutil.which("code.exe")
	if path_editor:
		candidates.append(Path(path_editor))
	local_app_data = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
	program_files = Path(os.getenv("PROGRAMFILES", r"C:\Program Files"))
	candidates.extend((
		local_app_data / "Programs" / "Microsoft VS Code" / "Code.exe",
		program_files / "Microsoft VS Code" / "Code.exe",
	))

	editor = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
	if editor is None:
		raise FileNotFoundError(
			"Could not find VS Code. Install it or set JARVIS_CODE_EDITOR to the full path of Code.exe."
		)
	return editor


def project_tool_info(name):
	tool_name = name.strip().casefold() if isinstance(name, str) else ""
	tool_name = PROJECT_TOOL_ALIASES.get(tool_name, tool_name)
	if tool_name not in PROJECT_TOOLS:
		allowed = ", ".join(sorted(PROJECT_TOOLS))
		raise ValueError(f"Unsupported project tool {name!r}. Choose from: {allowed}.")
	return tool_name, PROJECT_TOOLS[tool_name]


def required_tools_for_language(language):
	language_name = language.strip().casefold() if isinstance(language, str) else ""
	tool_name = PROJECT_TOOL_ALIASES.get(language_name, language_name)
	return (tool_name,) if tool_name in {
		"python", "nodejs", "dotnet", "java", "go", "rust",
	} else ()


def is_project_tool_installed(name):
	tool_name, tool = project_tool_info(name)
	if tool_name == "vscode":
		try:
			_vscode_executable()
		except FileNotFoundError:
			return False
		return True
	return any(shutil.which(command) for command in tool["commands"])


def install_project_tool(name):
	tool_name, tool = project_tool_info(name)
	if is_project_tool_installed(tool_name):
		return f"{tool['label']} is already available."
	winget = shutil.which("winget.exe") or shutil.which("winget")
	if winget is None:
		raise FileNotFoundError(
			"WinGet is not available. No installer was downloaded or run."
		)

	command = [
		winget,
		"install",
		"--id",
		tool["package_id"],
		"--exact",
		"--source",
		"winget",
		"--accept-source-agreements",
		"--accept-package-agreements",
	]
	try:
		result = subprocess.run(
			command,
			capture_output=True,
			text=True,
			timeout=600,
			check=False,
			shell=False,
		)
	except subprocess.TimeoutExpired as exc:
		raise TimeoutError(
			f"WinGet installation of {tool['label']} exceeded 10 minutes."
		) from exc
	if result.returncode != 0:
		output = (result.stderr or result.stdout or "").strip()
		raise RuntimeError(
			f"WinGet could not install {tool['label']} "
			f"(exit code {result.returncode}). {output[:800]}"
		)
	return (
		f"WinGet completed installation of {tool['label']}. Restart JARVIS or VS Code "
		"if Windows has not refreshed the PATH yet."
	)


def open_vscode(workspace):
	workspace_path = Path(workspace).resolve()
	if not workspace_path.is_dir():
		raise NotADirectoryError(f"Workspace folder does not exist: {workspace_path}")
	editor = _vscode_executable()
	subprocess.Popen([str(editor), str(workspace_path)], shell=False)
	return f"Opened {workspace_path.name or workspace_path} in VS Code."


def open_vscode_file(file_path):
	target = Path(file_path).resolve()
	if not target.is_file():
		raise FileNotFoundError(f"File does not exist: {target}")
	editor = _vscode_executable()
	subprocess.Popen([str(editor), str(target)], shell=False)
	return f"Opened {target.name} in VS Code."
