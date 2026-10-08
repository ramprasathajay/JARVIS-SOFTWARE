import os
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path


CONFIRMATION_TERMS = {"buy", "delete", "install", "pay", "purchase", "remove", "rename", "subscribe", "uninstall"}
CONFIRMED_OPERATIONS = {"click", "double-click", "right-click", "hold", "drag", "delete", "rename", "press", "open"}
HIGH_RISK_TERMS = CONFIRMATION_TERMS | {
	"clear", "empty", "erase", "format", "installer", "overwrite", "reset", "setup", "wipe",
}
KEYS = {
	"enter": "{ENTER}",
	"tab": "{TAB}",
	"escape": "{ESC}",
	"backspace": "{BACKSPACE}",
	"delete": "{DELETE}",
	"f2": "{F2}",
	"ctrl+a": "^a",
	"ctrl+c": "^c",
	"ctrl+x": "^x",
	"ctrl+v": "^v",
	"ctrl+z": "^z",
	"ctrl+y": "^y",
	"ctrl+s": "^s",
	"ctrl+f": "^f",
	"alt+f4": "%{F4}",
	"f5": "{F5}",
}


def requires_explicit_confirmation(request):
	normalized = (request or "").strip()
	normalized = re.sub(r"^double\s+(?:tap|click)\s+", "double-click ", normalized, flags=re.IGNORECASE)
	normalized = re.sub(r"^right\s+(?:tap|click)\s+", "right-click ", normalized, flags=re.IGNORECASE)
	normalized = re.sub(r"^long\s+press\s+", "hold ", normalized, flags=re.IGNORECASE)
	normalized = re.sub(r"^(?:key|press\s+key)\s+", "press ", normalized, flags=re.IGNORECASE)
	normalized = re.sub(r"^press\s+(?:control|ctrl)\s+([a-z])$", r"press ctrl+\1", normalized, flags=re.IGNORECASE)
	normalized = re.sub(
		r"^press\s+control\s+(enter|delete|f2|f5|c|x|v|a|f|s|z|y)$",
		lambda match: "press ctrl+" + match.group(1) if match.group(1).casefold() in {"c", "x", "v", "a", "f", "s", "z", "y"} else "press " + match.group(1),
		normalized,
		flags=re.IGNORECASE,
	)
	normalized = re.sub(r"^press\s+alt\s+f4$", "press alt+f4", normalized, flags=re.IGNORECASE)
	operation, separator, value = normalized.partition(" ")
	if not separator or operation.lower() not in CONFIRMED_OPERATIONS:
		return False
	if operation.lower() == "press" and value.casefold() in {"enter", "delete", "f2", "alt+f4"}:
		return True
	return any(
		re.search(rf"\b{re.escape(term)}\b", value, re.IGNORECASE)
		for term in HIGH_RISK_TERMS
	) or operation.lower() in {"delete", "rename"}


def desktop_control_locked():
	return os.getenv("JARVIS_DISABLE_COMMANDS", "0").lower() in {"1", "true", "yes"} or (
		os.getenv("JARVIS_ENABLE_COMMANDS", "0").lower() not in {"1", "true", "yes"}
	)


class ForegroundAppController:
	def __init__(self):
		self._target_handle = None
		self._target_title = ""
		self._lock = threading.RLock()

	def track_foreground(self):
		if os.name != "nt":
			return "Windows desktop control is unavailable on this platform."
		import win32gui
		import win32process

		handle = win32gui.GetForegroundWindow()
		_, process_id = win32process.GetWindowThreadProcessId(handle)
		title = win32gui.GetWindowText(handle).strip()
		with self._lock:
			if handle and process_id != os.getpid() and title:
				self._target_handle = handle
				self._target_title = title
				return title
			self._target_handle = None
			self._target_title = ""
			return "No other application window detected."

	def target_snapshot(self):
		with self._lock:
			return self._target_title, self._target_handle

	def list_open_apps(self):
		if os.name != "nt":
			raise RuntimeError("Windows app discovery is unavailable on this platform.")

		return sorted(
			{title for _, title in self._visible_app_windows()},
			key=str.casefold,
		)

	def resolve_app_window(self, label):
		self._validate_label(label)
		if os.name != "nt":
			raise RuntimeError("Windows app discovery is unavailable on this platform.")

		windows = [(title, handle) for handle, title in self._visible_app_windows()]
		matches = self._match_app_labels(label, windows, lambda item: item[0])
		if not matches:
			raise ValueError(f"No open app window matching '{label}' was found.")
		if len(matches) != 1:
			names = ", ".join(sorted({title for title, _ in matches}, key=str.casefold))
			raise ValueError(f"Several open app windows match '{label}': {names}. Use a more specific title.")
		return matches[0]

	def _visible_app_windows(self):
		import win32gui
		import win32process

		current_process_id = os.getpid()
		windows = []

		def collect_window(handle, _):
			if win32gui.IsWindowVisible(handle):
				title = win32gui.GetWindowText(handle).strip()
				_, process_id = win32process.GetWindowThreadProcessId(handle)
				if title and process_id != current_process_id:
					windows.append((handle, title))
			return True

		win32gui.EnumWindows(collect_window, None)
		return windows

	@staticmethod
	def _normalize_app_label(label):
		return re.sub(r"[^\w]+", " ", label.casefold(), flags=re.UNICODE).strip()

	@classmethod
	def _match_app_labels(cls, label, candidates, get_label):
		normalized_label = cls._normalize_app_label(label)
		if not normalized_label:
			return []

		normalized_candidates = [
			(candidate, cls._normalize_app_label(get_label(candidate)))
			for candidate in candidates
		]
		exact = [
			candidate for candidate, normalized in normalized_candidates
			if normalized == normalized_label
		]
		if exact:
			return exact
		partial = [
			candidate for candidate, normalized in normalized_candidates
			if normalized_label in normalized
		]
		if partial:
			return partial

		compact_label = normalized_label.replace(" ", "")
		if len(compact_label) < 4:
			return []
		label_word_count = len(normalized_label.split())
		ranked = []
		for candidate, normalized in normalized_candidates:
			words = normalized.split()
			fragments = {normalized.replace(" ", "")}
			for width in range(max(1, label_word_count - 1), label_word_count + 2):
				fragments.update(
					"".join(words[start:start + width])
					for start in range(max(0, len(words) - width + 1))
				)
			score = max(
				(
					SequenceMatcher(None, compact_label, fragment).ratio()
					for fragment in fragments
				),
				default=0,
			)
			if score >= 0.8:
				ranked.append((score, candidate))
		if not ranked:
			return []
		best_score = max(score for score, _ in ranked)
		return [
			candidate for score, candidate in ranked
			if best_score - score < 0.04
		]

	def close_app_window(self, label):
		if desktop_control_locked():
			return "App closing is locked by JARVIS_ENABLE_COMMANDS or JARVIS_DISABLE_COMMANDS."
		title, handle = self.resolve_app_window(label)
		import win32con
		import win32gui

		win32gui.PostMessage(handle, win32con.WM_CLOSE, 0, 0)
		return f"Sent a normal close request to '{title}'. The app may ask you to save changes."

	def clear_target(self):
		with self._lock:
			self._target_handle = None
			self._target_title = ""

	def _target_window(self, expected_handle=None, app_label=None, activate=True):
		if os.name != "nt":
			raise RuntimeError("Windows desktop control is unavailable on this platform.")
		import win32gui
		import win32process
		from pywinauto import Application

		if app_label is not None:
			_, target_handle = self.resolve_app_window(app_label)
		elif expected_handle is not None:
			if not win32gui.IsWindow(expected_handle):
				raise RuntimeError("The confirmed target window closed. Select the app again and retry.")
			target_handle = expected_handle
		else:
			active_handle = win32gui.GetForegroundWindow()
			_, process_id = win32process.GetWindowThreadProcessId(active_handle)
			active_title = win32gui.GetWindowText(active_handle).strip()
			with self._lock:
				if active_handle and process_id != os.getpid() and active_title:
					self._target_handle = active_handle
					self._target_title = active_title
				target_handle = self._target_handle
		if not target_handle or not win32gui.IsWindow(target_handle):
			raise RuntimeError("Open the app you want to control, then try again.")
		if activate:
			win32gui.ShowWindow(target_handle, 9)
			win32gui.SetForegroundWindow(target_handle)
		app = Application(backend="uia").connect(handle=target_handle, timeout=5)
		window = app.window(handle=target_handle).wrapper_object()
		title = win32gui.GetWindowText(target_handle).strip()
		if not title or win32process.GetWindowThreadProcessId(target_handle)[1] == os.getpid():
			raise RuntimeError("JARVIS will not control its own window or an inaccessible target.")
		with self._lock:
			self._target_handle = target_handle
			self._target_title = title
		return title, window

	def observe(self, expected_handle=None, app_label=None):
		with self._lock:
			title, window = self._target_window(
				expected_handle=expected_handle,
				app_label=app_label,
				activate=False,
			)
			controls = []
			unreadable_controls = 0
			for control in window.descendants():
				try:
					name = (control.element_info.name or "").strip()
					if name and control.is_visible():
						controls.append(f"{control.element_info.control_type}: {name[:100]}")
				except Exception:
					unreadable_controls += 1
			if unreadable_controls:
				controls.append(
					f"Inspection warning: {unreadable_controls} UI control(s) were stale or inaccessible."
				)
			return title, controls[:80]

	@staticmethod
	def _find_control(window, label):
		controls = [
			control for control in window.descendants()
			if control.is_visible() and control.is_enabled()
		]
		exact = [
			control for control in controls
			if (control.element_info.name or "").strip().casefold() == label.casefold()
		]
		matches = exact or [
			control for control in controls
			if label.casefold() in (control.element_info.name or "").strip().casefold()
		]
		if not matches:
			raise ValueError(f"No visible control matching '{label}' was found.")
		if len(matches) != 1:
			raise ValueError(f"Several controls match '{label}'; use a more specific label.")
		return matches[0]

	@staticmethod
	def _find_installed_app_shortcuts(label):
		roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
		program_data = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
		roots = (
			roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs",
			program_data / "Microsoft" / "Windows" / "Start Menu" / "Programs",
			roaming / "Programs",
			program_data / "Programs",
		)
		shortcuts = {
			path
			for root in roots
			if root.is_dir()
			for path in root.rglob("*.lnk")
			if path.is_file()
		}
		matches = ForegroundAppController._match_app_labels(label, shortcuts, lambda path: path.stem)
		if matches:
			return matches
		if not label:
			return []
		normalized_label = ForegroundAppController._normalize_app_label(label)
		if not normalized_label:
			return []
		candidates = []
		for path in sorted(shortcuts, key=lambda item: item.stem.casefold()):
			candidate_label = ForegroundAppController._normalize_app_label(path.stem)
			if normalized_label in candidate_label or candidate_label in normalized_label:
				candidates.append(path)
		return candidates

	def _launch_installed_app(self, label):
		if os.name != "nt":
			return None
		matches = self._find_installed_app_shortcuts(label)
		if not matches:
			return None
		if len(matches) != 1:
			names = ", ".join(sorted(path.stem for path in matches))
			raise ValueError(f"Several installed apps match '{label}': {names}. Use a more specific name.")
		os.startfile(str(matches[0]))
		return f"Opened installed app '{matches[0].stem}' from the Start Menu."

	def launch_installed_app(self, label):
		if desktop_control_locked():
			return "App launching is locked by JARVIS_ENABLE_COMMANDS or JARVIS_DISABLE_COMMANDS."
		self._validate_label(label)
		with self._lock:
			result = self._launch_installed_app(label)
		if result is None:
			return f"No installed Start Menu app matching '{label}' was found."
		return result

	@staticmethod
	def _validate_label(label):
		if not label or len(label) > 120 or any(char in label for char in "{}\n\r"):
			raise ValueError("Control labels must contain 1 to 120 printable characters.")

	def _execute_locked(self, request, confirmed, expected_handle=None, app_label=None):
		aliases = (
			(r"^double\s+(?:tap|click)\s+", "double-click "),
			(r"^right\s+(?:click|tap)\s+", "right-click "),
			(r"^tap\s+", "click "),
			(r"^long\s+press\s+", "hold "),
			(r"^(?:key|press\s+key)\s+", "press "),
			(r"^(?:move\s+mouse\s+to|move\s+pointer\s+to)\s+", "move "),
		)
		normalized_request = request.strip()
		for pattern, replacement in aliases:
			normalized_request = re.sub(pattern, replacement, normalized_request, count=1, flags=re.IGNORECASE)
		normalized_request = re.sub(
			r"^press\s+(?:control|ctrl)\s+([a-z])$",
			r"press ctrl+\1",
			normalized_request,
			flags=re.IGNORECASE,
		)
		normalized_request = re.sub(
			r"^press\s+control\s+(enter|delete|f2|f5|c|x|v|a|f|s|z|y)$",
			r"press ctrl+\1" if normalized_request.casefold().endswith((" c", " x", " v", " a", " f", " s", " z", " y")) else r"press \1",
			normalized_request,
			flags=re.IGNORECASE,
		)
		normalized_request = re.sub(r"^press\s+alt\s+f4$", "press alt+f4", normalized_request, flags=re.IGNORECASE)
		request = normalized_request
		operation, separator, value = (request or "").strip().partition(" ")
		operation = operation.lower()
		value = value.strip()
		allowed_operations = {
			"status", "scroll", "search", "click", "double-click", "right-click",
			"hold", "move", "drag", "type", "press", "copy", "cut", "paste",
			"delete", "rename", "open",
		}
		if operation == "refresh":
			operation, value = "press", "f5"
		if operation not in allowed_operations:
			return (
				"Desktop usage: screen status | scroll up/down | search <text> | "
				"click/double-click/right-click/hold/move <label> | drag <source> to <target> | "
				"type <text> | press <key> | copy/cut/paste | rename/delete <label>."
			)
		if requires_explicit_confirmation(request) and not confirmed:
			return "This desktop action requires a separate confirmation before it can run."
		try:
			target_options = {}
			if expected_handle is not None:
				target_options["expected_handle"] = expected_handle
			if app_label is not None:
				target_options["app_label"] = app_label
			title, window = self._target_window(**target_options)
			if operation == "status":
				_, controls = self.observe(
					expected_handle=expected_handle,
					app_label=app_label,
				)
				visible_controls = "; ".join(controls[:30]) or "No named controls were exposed."
				return f"Foreground app: {title}. Accessible controls: {visible_controls}"
			if operation == "scroll":
				if value not in {"up", "down"}:
					return "Say screen scroll up or screen scroll down."
				window.set_focus()
				window.wheel_mouse_input(wheel_dist=3 if value == "up" else -3)
				return f"Scrolled {value} in {title}."
			if operation == "search":
				if not value or len(value) > 160:
					return "Search text must contain between 1 and 160 characters."
				if any(char in value for char in "{}\n\r"):
					return "Search text cannot include braces or line breaks."
				edits = [
					control for control in window.descendants(control_type="Edit")
					if control.is_visible() and control.is_enabled()
				]
				search_edits = [
					control for control in edits
					if "search" in (control.element_info.name or "").casefold()
				]
				target = search_edits[0] if len(search_edits) == 1 else (edits[0] if len(edits) == 1 else None)
				if target is None:
					return "I could not identify one search field. Say screen status to inspect available controls."
				target.click_input()
				target.type_keys("^a", set_foreground=True)
				target.type_keys(value, with_spaces=True, vk_packet=True)
				target.type_keys("{ENTER}")
				return f"Searched {title} for {value}."
			if operation == "type":
				if not value or len(value) > 500 or any(char in value for char in "{}\n\r"):
					return "Text must contain 1 to 500 printable characters."
				focused_edits = [
					control for control in window.descendants(control_type="Edit")
					if control.is_visible() and control.has_keyboard_focus()
				]
				if len(focused_edits) != 1:
					return "Focus an editable text field in the target app first."
				focused_edits[0].type_keys(value, with_spaces=True, vk_packet=True)
				return f"Typed into the focused field in {title}."
			if operation in {"copy", "cut", "paste"}:
				window.set_focus()
				window.type_keys({"copy": "^c", "cut": "^x", "paste": "^v"}[operation])
				return f"Sent {operation} to the focused control in {title}. Clipboard contents were not read."
			if operation == "press":
				key = value.casefold()
				if key not in KEYS:
					return f"Allowed keys: {', '.join(KEYS)}."
				window.set_focus()
				window.type_keys(KEYS[key])
				return f"Pressed {key} in {title}."
			if operation == "drag":
				pair = re.fullmatch(r"(.+?)\s+to\s+(.+)", value, re.IGNORECASE)
				if not pair:
					return "Use screen drag <source label> to <target label>."
				source_label, target_label = pair.groups()
				self._validate_label(source_label)
				self._validate_label(target_label)
				source = self._find_control(window, source_label)
				target = self._find_control(window, target_label)
				source_rect = source.rectangle()
				target_rect = target.rectangle()
				source_point = (source_rect.left + source_rect.width() // 2, source_rect.top + source_rect.height() // 2)
				target_point = (target_rect.left + target_rect.width() // 2, target_rect.top + target_rect.height() // 2)
				source.drag_mouse_input(dst=target_point, src=source_point)
				return f"Dragged '{source_label}' to '{target_label}' in {title}."
			if operation in {"click", "double-click", "right-click", "hold", "move", "delete", "rename", "open"}:
				if operation == "rename":
					rename_match = re.fullmatch(r"(.+?)\s+to\s+(.+)", value, re.IGNORECASE)
					if not rename_match:
						return "Use screen rename <current item label> to <new name>."
					value, new_name = rename_match.groups()
					if (
						not new_name or len(new_name) > 255
						or any(char in new_name for char in '<>:"/\\|?*{}\n\r')
					):
						return "The new name must be 1-255 characters and cannot contain Windows filename separators or reserved characters."
				self._validate_label(value)
				try:
					control = self._find_control(window, value)
				except ValueError as exc:
					if (
						app_label is not None
						or operation != "click"
						or not str(exc).startswith("No visible control matching")
					):
						raise
					launch_result = self._launch_installed_app(value)
					if launch_result is not None:
						return launch_result
					return (
						f"No visible control matching '{value}' was found, and no matching "
						"installed Start Menu app was found."
					)
				if operation == "click":
					control.click_input()
				elif operation == "double-click" or operation == "open":
					control.double_click_input()
				elif operation == "right-click":
					control.right_click_input()
				elif operation == "hold":
					control.press_mouse_input(button="left")
					try:
						threading.Event().wait(0.7)
					finally:
						control.release_mouse_input(button="left")
				elif operation == "move":
					rect = control.rectangle()
					point = (rect.left + rect.width() // 2, rect.top + rect.height() // 2)
					control.move_mouse_input(coords=point, absolute=True)
				else:
					control.click_input()
					window.set_focus()
					if operation == "delete":
						window.type_keys("{DELETE}")
					else:
						window.type_keys("{F2}")
						window.type_keys(new_name, with_spaces=True, vk_packet=True)
						window.type_keys("{ENTER}")
				if operation == "click":
					return f"Clicked {control.element_info.control_type} '{value}' in {title}."
				return f"Performed {operation} on '{value}' in {title}."
		except Exception as exc:
			return f"Desktop action failed: {exc}"

	def execute(self, request, confirmed=False, expected_handle=None, app_label=None):
		if desktop_control_locked():
			return "Desktop control is locked by JARVIS_ENABLE_COMMANDS or JARVIS_DISABLE_COMMANDS."
		if requires_explicit_confirmation(request) and not confirmed:
			return "This desktop action requires a separate confirmation before it can run."
		with self._lock:
			return self._execute_locked(
				request,
				confirmed,
				expected_handle=expected_handle,
				app_label=app_label,
			)