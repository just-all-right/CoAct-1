import argparse

from mm_agents.coact.autogen import LLMConfig


DEFAULT_MODEL = "qwen3.8-flash"
DEFAULT_CONFIG_PATH = "OAI_CONFIG_LIST_QWEN"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the CoAct Qwen LLM config without making an API call.")
    parser.add_argument("--oai_config_path", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    for role in ("orchestrator", "coding", "summarizer"):
        config = LLMConfig.from_json(path=args.oai_config_path).where(model=args.model)
        entry = config.config_list[0]
        print(f"{role}: model={entry.model}, api_type={entry.api_type}, base_url={entry.base_url}")


if __name__ == "__main__":
    main()
