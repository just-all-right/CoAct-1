"""Run a short, non-destructive GUI-Plus smoke test against an existing DesktopEnv."""

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mm_agents.coact.cua_agent.qwen_gui_cua_agent import run_qwen_gui_cua
from desktop_env.desktop_env import DesktopEnv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path_to_vm", required=True)
    parser.add_argument("--save_path", default="./gui_plus_smoke")
    parser.add_argument("--provider_name", default="docker")
    args = parser.parse_args()

    env = DesktopEnv(
        path_to_vm=args.path_to_vm,
        action_space="pyautogui",
        provider_name=args.provider_name,
        os_type="Ubuntu",
        screen_size=(1920, 1080),
        headless=True,
        enable_proxy=True,
    )
    run_qwen_gui_cua(
        env,
        "Open the text editor.",
        max_steps=5,
        save_path=args.save_path,
        screen_width=1920,
        screen_height=1080,
    )


if __name__ == "__main__":
    main()