from __future__ import annotations

import base64
import json
import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from openai import OpenAI

from .gui_plus_prompt import GUI_PLUS_SYSTEM_PROMPT, validate_gui_plus_prompt

if TYPE_CHECKING:
    from desktop_env.desktop_env import DesktopEnv


# DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_BASE_URL = "https://llm-nop9yjkchw70a7mr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"

def parse_tool_call(text: str) -> dict:
    start = text.find("<tool_call>")
    end = text.find("</tool_call>", start + len("<tool_call>"))
    if start == -1 or end == -1:
        raise ValueError("GUI-Plus response does not contain a complete <tool_call> block.")

    payload = text[start + len("<tool_call>"):end].strip()
    try:
        tool_call = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in GUI-Plus <tool_call>: {exc}") from exc

    if not isinstance(tool_call, dict) or tool_call.get("name") != "computer_use":
        raise ValueError("GUI-Plus tool call must have name='computer_use'.")
    arguments = tool_call.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("GUI-Plus computer_use tool call must contain an arguments object.")
    return arguments


def map_coordinate(coord: Any, screen_width: int, screen_height: int) -> Tuple[int, int]:
    if not isinstance(coord, (list, tuple)) or len(coord) != 2:
        raise ValueError("coordinate must be a two-item list or tuple.")
    try:
        x = int(float(coord[0]) / 1000.0 * screen_width)
        y = int(float(coord[1]) / 1000.0 * screen_height)
    except (TypeError, ValueError) as exc:
        raise ValueError("coordinate values must be numeric.") from exc
    return max(0, min(screen_width - 1, x)), max(0, min(screen_height - 1, y))


def _keys(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ValueError("key action requires a string or list of strings.")


def action_to_pyautogui(args: dict, screen_width: int, screen_height: int) -> str:
    if not isinstance(args, dict):
        raise ValueError("GUI-Plus action arguments must be an object.")
    action = args.get("action")
    if not isinstance(action, str):
        raise ValueError("GUI-Plus action is missing a string 'action' field.")

    if action in {"left_click", "right_click", "middle_click", "double_click", "triple_click", "mouse_move"}:
        x, y = map_coordinate(args.get("coordinate"), screen_width, screen_height)
        if action == "mouse_move":
            return f"pyautogui.moveTo({x}, {y})"
        if action == "double_click":
            return f"pyautogui.doubleClick({x}, {y})"
        if action == "triple_click":
            return f"pyautogui.click({x}, {y}, clicks=3, interval=0.1)"
        button = action.removesuffix("_click")
        return f"pyautogui.click({x}, {y}, button='{button}')"

    if action == "left_click_drag":
        x, y = map_coordinate(args.get("coordinate"), screen_width, screen_height)
        return f"pyautogui.dragTo({x}, {y}, duration=1.0, button='left')"

    if action == "key":
        keys = [key.lower() for key in _keys(args.get("key", args.get("keys")))]
        if len(keys) == 1:
            return f"pyautogui.press({keys[0]!r})"
        return "pyautogui.hotkey(" + ", ".join(repr(key) for key in keys) + ")"

    if action == "type":
        # TODO: pyautogui.write does not reliably support Unicode/CJK.
        return f"pyautogui.write({str(args.get('text', args.get('content', '')) or '')!r})"

    if action in {"scroll", "hscroll"}:
        amount = args.get("pixels", 0)
        try:
            amount = int(amount)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{action} amount must be numeric.") from exc
        if action == "hscroll":
            return f"pyautogui.hscroll({amount})"
        return f"pyautogui.scroll({amount})"

    if action in {"wait", "terminate", "answer", "interact"}:
        return action.upper()

    raise ValueError(f"Unsupported GUI-Plus action: {action}")


def _response_text(response: Any) -> str:
    content = response.choices[0].message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(content)


def _redact_images(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    redacted = json.loads(json.dumps(history))
    for entry in redacted:
        for item in entry.get("content", []) if isinstance(entry.get("content"), list) else []:
            if isinstance(item, dict) and item.get("type") == "image_url":
                image = item.get("image_url")
                if isinstance(image, dict):
                    image["url"] = "<image>"
    return redacted


def run_qwen_gui_cua(
    env: DesktopEnv,
    instruction: str,
    max_steps: int,
    save_path: str = "./",
    screen_width: int = 1920,
    screen_height: int = 1080,
    sleep_after_execution: float = 0.5,
    truncate_history_inputs: int = 30,
    client_password: str = "",
    cua_client_config: Optional[dict] = None,
    cua_model: str = "gui-plus-2026-02-26",
) -> Tuple[List[Dict[str, Any]], str, float]:
    del client_password
    validate_gui_plus_prompt()
    config = dict(cua_client_config or {})
    api_key = config.pop("api_key", None) or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required for GUI-Plus.")
    base_url = config.pop("base_url", None) or os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL)
    client = OpenAI(api_key=api_key, base_url=base_url, **config)

    os.makedirs(save_path, exist_ok=True)
    current_screenshot = env.controller.get_screenshot()
    if current_screenshot is None:
        raise RuntimeError("Failed to capture initial screenshot from environment.")
    with open(os.path.join(save_path, "initial_screenshot.png"), "wb") as file:
        file.write(current_screenshot)

    encoded = base64.b64encode(current_screenshot).decode("utf-8")
    history_inputs: List[Dict[str, Any]] = [
        {"role": "system", "content": GUI_PLUS_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ],
        },
    ]
    total_cost = 0.0
    usage_records: List[Dict[str, int]] = []
    last_result = ""

    for step_idx in range(1, max_steps + 1):
        request_history = history_inputs[:2] + history_inputs[2:][-max(1, truncate_history_inputs):]
        response = client.chat.completions.create(
            model=cua_model,
            messages=request_history,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
            if prompt_tokens is not None and completion_tokens is not None:
                usage_records.append(
                    {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
                )
                print(f"[GUI-Plus] step={step_idx} prompt_tokens={prompt_tokens} completion_tokens={completion_tokens}")

        response_text = _response_text(response)
        history_inputs.append({"role": "assistant", "content": response_text})
        args = parse_tool_call(response_text)
        action = args.get("action")
        command = action_to_pyautogui(args, screen_width, screen_height)

        if action == "terminate":
            success = args.get("status") == "success"
            last_result = "TERMINATE: GUI-Plus reported success." if success else "UNEXPECTED: GUI-Plus reported failure."
            break
        if action == "answer":
            last_result = f"TERMINATE: {args.get('text', '')}".strip()
            break
        if action == "interact":
            last_result = f"UNEXPECTED: GUI-Plus requested user interaction: {args.get('text', '')}".strip()
            break

        if command == "WAIT":
            time.sleep(max(float(args.get("time", sleep_after_execution)), 0.0))
            next_screenshot = env.controller.get_screenshot()
        else:
            obs_dict, *_ = env.step(command, sleep_after_execution)
            next_screenshot = obs_dict.get("screenshot") if isinstance(obs_dict, dict) else None
        if next_screenshot is None:
            raise RuntimeError("DesktopEnv did not return a screenshot after GUI-Plus action.")

        with open(os.path.join(save_path, f"step_{step_idx}.png"), "wb") as file:
            file.write(next_screenshot)
        encoded = base64.b64encode(next_screenshot).decode("utf-8")
        history_inputs.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Action executed: {action}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                ],
            }
        )

    if not last_result:
        last_result = "UNEXPECTED: GUI-Plus reached max steps."
    with open(os.path.join(save_path, "usage.json"), "w") as file:
        json.dump(usage_records, file, indent=2)
    return _redact_images(history_inputs), last_result, total_cost