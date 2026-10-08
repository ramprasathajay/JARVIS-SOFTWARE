import os
from pathlib import Path
import threading


IGNORED_DIRECTORIES = {".git", ".venv", "__pycache__", "node_modules", "venv"}
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".odt", ".pdf", ".rtf", ".txt", ".md"}


def resolve_new_file(root, relative_path):
	root = Path(root).resolve()
	requested_path = Path(relative_path)
	if not relative_path or requested_path.is_absolute():
		raise ValueError("Choose a relative file path inside the selected project.")
	target = (root / requested_path).resolve()
	try:
		target.relative_to(root)
	except ValueError as exc:
		raise ValueError("The new file must stay inside the selected project.") from exc
	if target.exists():
		raise FileExistsError(target)
	return target


def scan_project(root):
	root = Path(root).resolve()
	snapshot = {}
	for current_root, directories, filenames in os.walk(root, followlinks=False):
		directories[:] = [
			name for name in directories
			if name not in IGNORED_DIRECTORIES
			and not (Path(current_root) / name).is_symlink()
		]
		for filename in filenames:
			path = Path(current_root) / filename
			try:
				if path.is_symlink() or not path.is_file():
					continue
				stat = path.stat()
				relative_path = path.relative_to(root).as_posix()
				snapshot[relative_path] = (stat.st_size, stat.st_mtime_ns)
			except OSError:
				continue
	return snapshot


def project_update(root, snapshot, previous=None, initial=False):
	previous = previous or {}
	created = sorted(snapshot.keys() - previous.keys()) if not initial else []
	deleted = sorted(previous.keys() - snapshot.keys())
	modified = sorted(
		path for path in snapshot.keys() & previous.keys()
		if snapshot[path] != previous[path]
	)
	return {
		"root": str(root),
		"files": len(snapshot),
		"documents": sum(
			Path(path).suffix.lower() in DOCUMENT_EXTENSIONS for path in snapshot
		),
		"created": created,
		"deleted": deleted,
		"modified": modified,
		"initial": initial,
	}


class ProjectFolderMonitor:
	def __init__(self, root, callback, interval=3.0):
		self.root = Path(root).resolve()
		self.callback = callback
		self.interval = interval
		self._stop_event = threading.Event()
		self._thread = None

	def start(self):
		if self._thread is not None and self._thread.is_alive():
			return
		self._stop_event.clear()
		self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-project-watch")
		self._thread.start()

	def stop(self):
		self._stop_event.set()
		if self._thread is not None and self._thread.is_alive():
			self._thread.join(timeout=1)

	def _run(self):
		previous = scan_project(self.root)
		self.callback(project_update(self.root, previous, initial=True))
		while not self._stop_event.wait(self.interval):
			current = scan_project(self.root)
			self.callback(project_update(self.root, current, previous))
			previous = current