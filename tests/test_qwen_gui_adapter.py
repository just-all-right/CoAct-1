import unittest

from mm_agents.coact.cua_agent.qwen_gui_cua_agent import (
    action_to_pyautogui,
    map_coordinate,
    parse_tool_call,
)


class QwenGuiAdapterTests(unittest.TestCase):
    def test_parse_tool_call(self):
        text = '<tool_call>\n{"name":"computer_use","arguments":{"action":"left_click","coordinate":[500,500]}}\n</tool_call>'
        self.assertEqual(parse_tool_call(text), {"action": "left_click", "coordinate": [500, 500]})


    def test_map_coordinate(self):
        self.assertEqual(map_coordinate([500, 500], 1920, 1080), (960, 540))
        self.assertEqual(map_coordinate([-1, 2000], 1920, 1080), (0, 1079))


    def test_action_mapping(self):
        command = action_to_pyautogui({"action": "left_click", "coordinate": [500, 500]}, 1920, 1080)
        self.assertEqual(command, "pyautogui.click(960, 540, button='left')")
        self.assertEqual(
            action_to_pyautogui({"action": "key", "key": ["CTRL", "S"]}, 1920, 1080),
            "pyautogui.hotkey('ctrl', 's')",
        )
        self.assertEqual(
            action_to_pyautogui({"action": "scroll", "pixels": -4}, 1920, 1080),
            "pyautogui.scroll(-4)",
        )
        self.assertEqual(
            action_to_pyautogui({"action": "left_click_drag", "coordinate": [500, 500]}, 1920, 1080),
            "pyautogui.dragTo(960, 540, duration=1.0, button='left')",
        )


    def test_invalid_tool_name(self):
        with self.assertRaisesRegex(ValueError, "computer_use"):
            parse_tool_call('<tool_call>{"name":"other","arguments":{}}</tool_call>')


    def test_termination_mapping(self):
        self.assertEqual(action_to_pyautogui({"action": "terminate"}, 1920, 1080), "TERMINATE")