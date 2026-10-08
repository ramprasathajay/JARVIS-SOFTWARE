import ipaddress
import queue
import re
import socket
import threading
from urllib.parse import urlsplit


class BrowserController:
	def __init__(self):
		self._commands = queue.Queue()
		self._thread = None
		self._lock = threading.Lock()
		self._closing = False
		self._close_response = None

	def execute(self, operation, value=""):
		response = queue.Queue(maxsize=1)
		with self._lock:
			if self._closing:
				raise RuntimeError("The controlled browser is shutting down.")
			if self._thread is None or not self._thread.is_alive():
				self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-browser")
				self._thread.start()
			self._commands.put((operation, value, response))
		return response.get()

	def close(self, timeout=60.0):
		with self._lock:
			thread = self._thread
			if thread is None or not thread.is_alive():
				self._thread = None
				self._closing = False
				self._close_response = None
				return "Browser controller is not running."
			if self._closing:
				response = self._close_response
				if response is None:
					raise RuntimeError("The browser shutdown response is unavailable.")
			else:
				response = queue.Queue(maxsize=1)
				self._closing = True
				self._close_response = response
				self._commands.put(("close", "", response))
		try:
			result = response.get(timeout=timeout)
		except queue.Empty:
			raise TimeoutError(
				f"Browser shutdown did not finish within {timeout:g} seconds. "
				"The app was kept open to avoid breaking the Playwright driver pipe."
			) from None
		if result.startswith("Browser shutdown incomplete:"):
			with self._lock:
				self._closing = False
				self._close_response = None
			return result
		thread.join(timeout=5)
		if thread.is_alive():
			raise TimeoutError(
				"Browser shutdown finished its command but the Playwright worker is still running."
			)
		with self._lock:
			if self._thread is thread:
				self._thread = None
			self._closing = False
			self._close_response = None
		return result

	def _run(self):
		playwright = None
		browser = None
		context = None
		page = None
		zoom_level = 100
		while True:
			operation, value, response = self._commands.get()
			if operation == "close":
				cleanup_errors = []
				for resource, close_method in ((context, "close"), (browser, "close")):
					if resource is not None:
						try:
							getattr(resource, close_method)()
						except Exception as exc:
							cleanup_errors.append(f"{close_method}: {exc}")
				driver_stopped = playwright is None
				if playwright is not None:
					try:
						playwright.stop()
						driver_stopped = True
					except Exception as exc:
						cleanup_errors.append(f"stop: {exc}")
				if not driver_stopped:
					response.put(
						"Browser shutdown incomplete: the Playwright driver could not be "
						"stopped. The browser worker remains available for a retry. "
						+ "; ".join(cleanup_errors)
					)
					continue
				if cleanup_errors:
					response.put(
						"Browser shutdown finished with cleanup warnings: "
						+ "; ".join(cleanup_errors)
					)
				else:
					response.put("Browser closed.")
				return
			try:
				if operation not in {"open", "open_tab"} and (page is None or page.is_closed()):
					response.put("Open a website first, then try that browser action again.")
					continue
				if playwright is None:
					from playwright.sync_api import sync_playwright

					playwright = sync_playwright().start()
				if browser is None or not browser.is_connected():
					browser = playwright.chromium.launch(
						channel="msedge",
						headless=False,
						args=["--inprivate"],
					)
					context = browser.new_context()
					context.route("**/*", self._guard_request)
				if operation == "open":
					if page is None or page.is_closed():
						page = context.new_page()
					page.goto(value, wait_until="domcontentloaded", timeout=30000)
					loaded = self._wait_for_page_load(page)
					status = "fully loaded" if loaded else "opened; some page resources are still loading"
					response.put(f"{page.url} {status} in the controlled browser.")
				elif operation == "open_tab":
					page = context.new_page()
					response.put("Opened a new InPrivate tab in the controlled browser.")
				elif operation == "next_tab":
					pages = [candidate for candidate in context.pages if not candidate.is_closed()]
					if len(pages) < 2:
						response.put("Only one controlled browser tab is open.")
					else:
						current_index = pages.index(page) if page in pages else -1
						page = pages[(current_index + 1) % len(pages)]
						page.bring_to_front()
						response.put(f"Switched to {page.url}.")
				elif operation == "scroll":
					distance = -650 if value == "up" else 650
					page.mouse.wheel(0, distance)
					response.put(f"Scrolled {value}.")
				elif operation in {"play", "pause"}:
					media = page.locator("video, audio")
					count = media.count()
					if not count:
						response.put("No audio or video player was found on the current page.")
					elif operation == "pause":
						media.evaluate_all("elements => elements.forEach(element => element.pause())")
						response.put("Paused media on the current page.")
					else:
						played = media.evaluate_all(
							"async elements => {\n"
							"  const results = await Promise.allSettled(elements.map(element => element.play()));\n"
							"  return results.some(result => result.status === 'fulfilled');\n"
							"}"
						)
						response.put(
							"Playing media on the current page."
							if played else "The page blocked media playback."
						)
				elif operation == "click":
					label = value.strip()
					if not label or len(label) > 120:
						response.put("Link or button text must contain between 1 and 120 characters.")
						continue
					matches = []
					for role in ("link", "button"):
						locator = page.get_by_role(role, name=label, exact=True)
						count = locator.count()
						if count == 0:
							locator = page.get_by_role(
								role, name=re.compile(re.escape(label), re.IGNORECASE)
							)
							count = locator.count()
						if count:
							matches.append((role, locator, count))
					total_matches = sum(count for _, _, count in matches)
					if total_matches == 0:
						response.put(f"No link or button matching '{label}' was found on the current page.")
					elif total_matches > 1:
						response.put(f"More than one link or button matches '{label}'. Use more specific text.")
					else:
						role, locator, _ = matches[0]
						locator.click(timeout=10000)
						page.wait_for_timeout(250)
						pages = [candidate for candidate in context.pages if not candidate.is_closed()]
						if pages:
							page = pages[-1]
							loaded = self._wait_for_page_load(page)
							status = "fully loaded" if loaded else "opened; some page resources are still loading"
							response.put(f"{page.url} {status} after clicking {role} '{label}'.")
						else:
							response.put(f"Clicked {role} '{label}'.")
				elif operation == "type":
					text = value
					if not text.strip() or len(text) > 500 or any(ord(char) < 32 and char not in "\t" for char in text):
						response.put("Text must contain 1 to 500 printable characters.")
						continue
					focused = page.locator(":focus")
					if focused.count() != 1:
						response.put("Focus a text field on the page before asking me to type.")
						continue
					editable = focused.evaluate(
						"""element => {
							const tag = element.tagName.toLowerCase();
							const inputType = (element.type || "").toLowerCase();
							const blockedTypes = ["button", "checkbox", "file", "hidden", "password", "radio", "reset", "submit"];
							return !element.disabled && !element.readOnly && (
								tag === "textarea" ||
								(tag === "input" && !blockedTypes.includes(inputType)) ||
								element.isContentEditable
							);
						}"""
					)
					if not editable:
						response.put("The focused element is not an editable text field.")
						continue
					page.keyboard.type(text)
					response.put("Typed into the focused text field.")
				elif operation == "fullscreen":
					session = context.new_cdp_session(page)
					try:
						window_id = session.send("Browser.getWindowForTarget")["windowId"]
						bounds = session.send("Browser.getWindowBounds", {"windowId": window_id})["bounds"]
						state = "normal" if bounds.get("windowState") == "fullscreen" else "fullscreen"
						session.send("Browser.setWindowBounds", {
							"windowId": window_id,
							"bounds": {"windowState": state},
						})
					finally:
						session.detach()
					response.put(f"Browser window set to {state}.")
				elif operation == "zoom" and value in {"in", "out"}:
					zoom_level = max(50, min(200, zoom_level + (10 if value == "in" else -10)))
					page.evaluate(
						"scale => { document.documentElement.style.zoom = `${scale}%`; }",
						zoom_level,
					)
					response.put(f"Page zoom set to {zoom_level}%.")
				else:
					response.put("Unsupported browser action.")
			except Exception as exc:
				response.put(f"Browser action failed: {exc}")

	@staticmethod
	def _wait_for_page_load(page):
		from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

		try:
			page.wait_for_load_state("load", timeout=15000)
		except PlaywrightTimeoutError:
			return False
		return True

	@staticmethod
	def _guard_request(route):
		url = route.request.url
		parsed = urlsplit(url)
		if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or not parsed.hostname:
			route.abort()
			return
		try:
			addresses = {
				ipaddress.ip_address(result[4][0])
				for result in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
			}
		except (OSError, ValueError):
			route.abort()
			return
		if not addresses or any(
			address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
			for address in addresses
		):
			route.abort()
			return
		route.continue_()


_controller = BrowserController()


def execute_browser_action(operation, value=""):
	return _controller.execute(operation, value)


def close_browser():
	return _controller.close()