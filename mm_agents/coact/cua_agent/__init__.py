__all__ = [
	"run_openai_cua",
	"run_claude_cua_bedrock",
	"run_claude_cua",
	"run_uitars_cua",
	"run_opencua_cua",
	"run_qwen_gui_cua",
]


def __getattr__(name):
	modules = {
		"run_openai_cua": ".openai_cua_agent",
		"run_claude_cua_bedrock": ".claude_cua_agent_bedrock",
		"run_claude_cua": ".claude_cua_agent",
		"run_uitars_cua": ".uitars_cua_agent",
		"run_opencua_cua": ".opencua_cua_agent",
		"run_qwen_gui_cua": ".qwen_gui_cua_agent",
	}
	if name not in modules:
		raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

	from importlib import import_module

	function = getattr(import_module(modules[name], __name__), name)
	globals()[name] = function
	return function