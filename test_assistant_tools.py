import json
from types import SimpleNamespace

import assistant_tools
import foog


class FakeGroqCompletions:
	def __init__(self, responses):
		self.responses = iter(responses)
		self.requests = []

	def create(self, **kwargs):
		self.requests.append(kwargs)
		return next(self.responses)


def _groq_response(message):
	return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _fail_if_tool_is_executed(name, arguments):
	raise AssertionError(f"Unexpected tool execution: {name} {arguments}")


def test_groq_tool_call_returns_to_model_and_then_finishes():
	tool_call = SimpleNamespace(
		id="call-1",
		function=SimpleNamespace(
			name="open_app",
			arguments=json.dumps({"name": "notepad"}),
		),
	)
	client = SimpleNamespace(
		chat=SimpleNamespace(
			completions=FakeGroqCompletions([
				_groq_response(SimpleNamespace(content=None, tool_calls=[tool_call])),
				_groq_response(SimpleNamespace(content="Opened Notepad.", tool_calls=[])),
			])
		)
	)
	executed = []

	reply = assistant_tools.generate_reply(
		client,
		"groq",
		"test-model",
		[{"role": "user", "content": "Open Notepad."}],
		lambda name, arguments: executed.append((name, arguments)) or {
			"ok": True,
			"message": "The app opened.",
		},
	)

	assert reply == "Opened Notepad."
	assert executed == [("open_app", {"name": "notepad"})]
	requests = client.chat.completions.requests
	assert requests[0]["tool_choice"] == "auto"
	assert requests[1]["messages"][-1]["role"] == "tool"
	assert json.loads(requests[1]["messages"][-1]["content"]) == {
		"ok": True,
		"message": "The app opened.",
	}


def test_groq_invalid_tool_json_is_returned_as_a_tool_error():
	tool_call = SimpleNamespace(
		id="call-invalid",
		function=SimpleNamespace(name="open_app", arguments="{"),
	)
	client = SimpleNamespace(
		chat=SimpleNamespace(
			completions=FakeGroqCompletions([
				_groq_response(SimpleNamespace(content=None, tool_calls=[tool_call])),
				_groq_response(SimpleNamespace(content="I could not open it.", tool_calls=[])),
			])
		)
	)

	assert assistant_tools.generate_reply(
		client,
		"groq",
		"test-model",
		[{"role": "user", "content": "Open Notepad."}],
		_fail_if_tool_is_executed,
	) == "I could not open it."
	tool_message = client.chat.completions.requests[1]["messages"][-1]
	assert json.loads(tool_message["content"])["ok"] is False


def test_google_tool_call_preserves_system_instruction_and_continues():
	types = foog.google_genai_types
	call = types.FunctionCall(name="browse_web", args={"target": "wikipedia"})
	model_content = types.Content(
		role="model",
		parts=[types.Part(function_call=call)],
	)
	responses = iter([
		SimpleNamespace(
			candidates=[SimpleNamespace(content=model_content)],
			text=None,
		),
		SimpleNamespace(candidates=[], text="Wikipedia is open."),
	])

	class FakeModels:
		def __init__(self):
			self.requests = []

		def generate_content(self, **kwargs):
			self.requests.append(kwargs)
			return next(responses)

	models = FakeModels()
	client = SimpleNamespace(models=models)
	executed = []

	reply = assistant_tools.generate_reply(
		client,
		"google",
		"test-model",
		[
			{"role": "system", "content": "Use guarded tools only."},
			{"role": "user", "content": "Open Wikipedia."},
		],
		lambda name, arguments: executed.append((name, arguments)) or {
			"ok": True,
			"message": "The site opened.",
		},
	)

	assert reply == "Wikipedia is open."
	assert executed == [("browse_web", {"target": "wikipedia"})]
	assert models.requests[0]["config"].system_instruction == "Use guarded tools only."
	assert len(models.requests[1]["contents"]) == 3


def test_groq_tool_loop_stops_at_round_limit():
	tool_call = SimpleNamespace(
		id="call-repeat",
		function=SimpleNamespace(
			name="open_app",
			arguments=json.dumps({"name": "notepad"}),
		),
	)
	client = SimpleNamespace(
		chat=SimpleNamespace(
			completions=FakeGroqCompletions([
				_groq_response(SimpleNamespace(content=None, tool_calls=[tool_call]))
				for _ in range(assistant_tools.MAX_TOOL_ROUNDS)
			])
		)
	)
	executed = []

	try:
		assistant_tools.generate_reply(
			client,
			"groq",
			"test-model",
			[{"role": "user", "content": "Keep opening Notepad."}],
			lambda name, arguments: executed.append((name, arguments)) or {
				"ok": True,
				"message": "The app opened.",
			},
		)
	except RuntimeError as exc:
		assert "maximum number of tool-call rounds" in str(exc)
	else:
		raise AssertionError("The tool loop should stop at its configured round limit.")
	assert len(executed) == assistant_tools.MAX_TOOL_ROUNDS
