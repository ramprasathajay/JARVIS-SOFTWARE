import json

import foog


MAX_TOOL_ROUNDS = 10
MAX_TOOL_CALLS = 20

TOOL_DEFINITIONS = [
	{
		"type": "function",
		"function": {
			"name": "list_apps",
			"description": (
				"List the visible, titled app windows open on this Windows desktop. "
				"Only call while the user has enabled Desktop Access. The host sends "
				"the window titles to the configured AI provider for this request."
			),
			"parameters": {
				"type": "object",
				"properties": {},
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "inspect_app",
			"description": (
				"Read the visible accessible control names for one open app window, "
				"or the current foreground app if app is omitted. Only call while "
				"Desktop Access is enabled; control names are sent to the configured "
				"AI provider for this request. Do not infer screen pixels or private "
				"content that accessibility controls do not expose."
			),
			"parameters": {
				"type": "object",
				"properties": {"app": {"type": "string", "maxLength": 120}},
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "open_app",
			"description": (
				"Open one approved application or one uniquely matched Start Menu app. "
				"The host asks for confirmation before launch. If the app is not installed, "
				"the host asks before searching Microsoft Store, then offers a separately "
				"approved web search. Never download or launch an installer without the "
				"user selecting the file and confirming its full path."
			),
			"parameters": {
				"type": "object",
				"properties": {"name": {"type": "string", "minLength": 1, "maxLength": 40}},
				"required": ["name"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "close_app",
			"description": (
				"Request a normal close of one uniquely matched open app window. "
				"The host asks the user for confirmation first; never force-terminate "
				"the app or claim it closed until the host confirms."
			),
			"parameters": {
				"type": "object",
				"properties": {"name": {"type": "string", "minLength": 1, "maxLength": 120}},
				"required": ["name"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "connect_code_workspace",
			"description": (
				"Use when the user asks to open VS Code for a project or work on code. Ask the host to "
				"let the user choose a project folder, then open that folder in VS Code. "
				"Use the connected workspace as the destination for requested project files."
			),
			"parameters": {
				"type": "object",
				"properties": {},
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "read_project_file",
			"description": (
				"After a project check fails, read only a relevant existing UTF-8 source "
				"file from the selected workspace to diagnose the failure and propose a "
				"focused fix. The host shows which file contents will be shared with the "
				"configured AI provider and asks the user first. Never read secrets, "
				"credentials, binaries, or files outside the workspace."
			),
			"parameters": {
				"type": "object",
				"properties": {"path": {"type": "string", "minLength": 1, "maxLength": 220}},
				"required": ["path"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "write_project_file",
			"description": (
				"Create or update one text source/config/document file in the selected "
				"VS Code workspace. Infer a conventional filename and extension from "
				"the project and user request. Provide complete file contents. The host "
				"shows a preview and requires confirmation before every write, including "
				"proposed fixes; never claim it was saved unless the host confirms."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"path": {"type": "string", "minLength": 1, "maxLength": 220},
					"content": {"type": "string", "minLength": 1, "maxLength": 100000},
				},
				"required": ["path", "content"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "run_project_check",
			"description": (
				"After implementing a requested code task or when the user asks to check a "
				"connected project, request a recognized check: auto, python-syntax, "
				"javascript-syntax, python-tests, or dotnet-tests. Prefer checks that do not execute project "
				"code. The host detects availability, displays the exact check and asks "
				"permission before running; tests execute project code. Report actual results. "
				"If a check fails, diagnose it using only relevant files after the host asks "
				"the user before sharing them, then propose fixes through per-file previews."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"check": {
						"type": "string",
						"enum": [
							"auto", "python-syntax", "javascript-syntax",
							"python-tests", "dotnet-tests",
						],
					},
				},
				"required": ["check"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "prepare_project_tools",
			"description": (
				"Before writing project files, identify the required language runtimes "
				"and tools and call this once with their names. Allowed names are vscode, "
				"python, nodejs, git, dotnet, java, go, rust, blender, and godot. The host checks availability, "
				"asks permission before any missing tool, then asks separately before "
				"installing each exact known WinGet package. Do not call with tools unrelated "
				"to the user's project."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"tools": {
						"type": "array",
						"items": {
							"type": "string",
							"enum": [
								"vscode", "python", "nodejs", "git", "dotnet",
								"java", "go", "rust", "blender", "godot",
							],
						},
						"maxItems": 10,
					},
				},
				"required": ["tools"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "browse_web",
			"description": (
				"Open a public website or search the web in JARVIS's controlled browser. "
				"Never use this to download, submit forms, or access private/local addresses."
			),
			"parameters": {
				"type": "object",
				"properties": {"target": {"type": "string", "minLength": 1, "maxLength": 240}},
				"required": ["target"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "desktop_action",
			"description": (
				"Perform one confirmed action in a visible open app window. Optionally "
				"set app to a specific window title; otherwise use the foreground app. "
				"Use visible accessible control names. Supported operations: scroll "
				"(target up/down), search (text), click, double-click, right-click, "
				"open (target), type (text), press (target key)."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"operation": {
						"type": "string",
						"enum": [
							"scroll", "search", "click", "double-click",
							"right-click", "open", "type", "press",
						],
					},
					"target": {"type": "string", "maxLength": 160},
					"text": {"type": "string", "maxLength": 500},
					"app": {"type": "string", "maxLength": 120},
				},
				"required": ["operation"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "browser_action",
			"description": (
				"Perform one confirmed action in an already-open JARVIS-controlled "
				"browser. Supported operations: scroll, click, type, play, pause, "
				"next_tab, open_tab (opens an InPrivate tab), fullscreen, zoom."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"operation": {
						"type": "string",
						"enum": [
							"scroll", "click", "type", "play", "pause", "next_tab",
							"open_tab", "fullscreen", "zoom",
						],
					},
					"value": {"type": "string", "maxLength": 500},
				},
				"required": ["operation"],
				"additionalProperties": False,
			},
		},
	},
]
ALLOWED_TOOL_NAMES = frozenset(
	tool["function"]["name"] for tool in TOOL_DEFINITIONS
)


def _tool_result(execute_tool, name, arguments):
	if not isinstance(arguments, dict):
		return {"ok": False, "message": "Tool arguments must be an object."}
	try:
		result = execute_tool(name, arguments)
	except Exception:
		return {
			"ok": False,
			"message": "The action failed. The host did not share local application details.",
		}
	if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
		raise TypeError("Tool executor must return a result object with an 'ok' boolean.")
	return result


def _generate_groq_reply(client, model, messages, execute_tool, temperature, max_tokens):
	history = [dict(message) for message in messages]
	tool_count = 0
	for _ in range(MAX_TOOL_ROUNDS):
		response = client.chat.completions.create(
			model=model,
			messages=history,
			temperature=temperature,
			max_tokens=max_tokens,
			tools=TOOL_DEFINITIONS,
			tool_choice="auto",
		)
		message = response.choices[0].message
		calls = list(message.tool_calls or [])
		if not calls:
			return (message.content or "").strip()
		assistant_message = {
			"role": "assistant",
			"content": message.content,
			"tool_calls": [
				{
					"id": call.id,
					"type": "function",
					"function": {
						"name": call.function.name,
						"arguments": call.function.arguments,
					},
				}
				for call in calls
			],
		}
		history.append(assistant_message)
		for call in calls:
			tool_count += 1
			if tool_count > MAX_TOOL_CALLS:
				raise RuntimeError("The assistant exceeded the tool-call limit for one request.")
			try:
				arguments = json.loads(call.function.arguments or "{}")
			except json.JSONDecodeError as exc:
				result = {"ok": False, "message": f"Invalid tool JSON arguments: {exc}"}
			else:
				result = _tool_result(execute_tool, call.function.name, arguments)
			history.append({
				"role": "tool",
				"tool_call_id": call.id,
				"content": json.dumps(result, ensure_ascii=False),
			})
	raise RuntimeError("The assistant reached the maximum number of tool-call rounds.")


def _generate_google_reply(client, model, messages, execute_tool, temperature, max_tokens):
	if foog.google_genai_types is None:
		raise RuntimeError("Google tool calling needs the installed google-genai types package.")
	types = foog.google_genai_types
	declarations = [
		types.FunctionDeclaration(
			name=tool["function"]["name"],
			description=tool["function"]["description"],
			parameters=tool["function"]["parameters"],
		)
		for tool in TOOL_DEFINITIONS
	]
	tools = [types.Tool(function_declarations=declarations)]
	contents = foog.format_google_messages(messages)
	system_instruction = "\n".join(
		message.get("content", "")
		for message in messages
		if message.get("role") == "system" and message.get("content")
	)
	tool_count = 0
	for _ in range(MAX_TOOL_ROUNDS):
		response = client.models.generate_content(
			model=model,
			contents=contents,
			config=types.GenerateContentConfig(
				temperature=temperature,
				max_output_tokens=max_tokens,
				system_instruction=system_instruction or None,
				tools=tools,
			),
		)
		candidate = response.candidates[0] if response.candidates else None
		content = getattr(candidate, "content", None) if candidate is not None else None
		parts = getattr(content, "parts", []) if content is not None else []
		calls = [
			part.function_call
			for part in parts
			if getattr(part, "function_call", None) is not None
		]
		if not calls:
			text = getattr(response, "text", None)
			if text:
				return text.strip()
			text_parts = [
				part.text for part in parts
				if getattr(part, "text", None)
			]
			if text_parts:
				return "".join(text_parts).strip()
			return ""

		contents.append(content)
		function_responses = []
		for call in calls:
			tool_count += 1
			if tool_count > MAX_TOOL_CALLS:
				raise RuntimeError("The assistant exceeded the tool-call limit for one request.")
			result = _tool_result(execute_tool, call.name, dict(call.args or {}))
			function_responses.append(
				types.Part.from_function_response(
					name=call.name,
					response=result,
				)
			)
		contents.append(types.Content(role="user", parts=function_responses))
	raise RuntimeError("The assistant reached the maximum number of tool-call rounds.")


def generate_reply(client, provider, model, messages, execute_tool, temperature=0.7, max_tokens=500):
	provider_name = (provider or foog.DEFAULT_PROVIDER).lower()
	if provider_name == "google":
		return _generate_google_reply(
			client, model, messages, execute_tool, temperature, max_tokens
		)
	return _generate_groq_reply(
		client, model, messages, execute_tool, temperature, max_tokens
	)
