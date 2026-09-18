from __future__ import annotations

import base64
import json
import os
import traceback
# from google import genai
from typing import Any, Callable, Literal, Optional, Union
from desktop_env.desktop_env import DesktopEnv
from desktop_env.providers import ProviderConfig

from .autogen.llm_config import LLMConfig
from .autogen.agentchat.conversable_agent import ConversableAgent
from .autogen.agentchat.contrib.multimodal_conversable_agent import MultimodalConversableAgent

from .coding_agent import TerminalProxyAgent, CODER_SYSTEM_MESSAGE, CONVERSATION_REVIEW_PROMPT


class OrchestratorAgent(MultimodalConversableAgent):
    """任务编排器的基类。

    这个类本质上是一个“调度员”：
    - 它把用户的高层目标拆成可以调用的工具；
    - 根据 mode 决定当前场景允许哪些能力；
    - 在 LLM 需要时，把任务交给 GUI Agent 或 Coding Agent 去执行。

    对新人来说，最重要的理解是：
    这里不直接完成具体操作，而是负责“决定调用谁，以及哪些能力开放给模型”。
    """

    CALL_GUI_AGENT_TOOL = {
        "type": "function",
        "function": {
            "name": "call_gui_operator",
            "description": """- Can interact with the OS by GUI operations, including clicking, scrolling, typing, and using hotkeys.
- After the task completed, a summarizer will summarize the task completion process.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Describe the target followed by a detailed, step-by-step task instructions.",
                    },
                },
            },
        },
    }

    CALL_CODING_AGENT_TOOL = {
        "type": "function",
        "function": {
            "name": "call_programmer",
            "description": """- Can run Python or Bash code to interact with the system.
- Needs a target description with detailed task instructions.
- Can use any Python package you specify.
- After modifying a file, ALWAYS verify every change by yourself.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Describe the target followed by a detailed, step-by-step task instructions."
                    },
                },
            },
        },
    }

    CALL_API_SUMMARY_AGENT_TOOL = {
        "type": "function",
        "function": {
            "name": "call_api_summary_agent",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "[REQUIRED] A url of the API response."},
                },
            },
        },
    }

    def __init__(
        self,
        name: str,
        mode: str = "coact",
        system_message: Optional[str] = None,
        llm_config: Optional[Union[LLMConfig, dict[str, Any], Literal[False]]] = None,
        is_termination_msg: Optional[Callable[[dict[str, Any]], bool]] = None,
        max_consecutive_auto_reply: Optional[int] = None,
        human_input_mode: Optional[str] = "NEVER",
        code_execution_config: Optional[Union[dict[str, Any], Literal[False]]] = False,
        description: Optional[str] = "",
        genai_client: Optional[genai.Client] = None,
        mimic_human_return: bool = True,
        **kwargs: Any,
    ):
        # 这个初始化阶段会先调用基类，建立通用的对话式 Agent 能力；
        # 接着根据 mode 只暴露某些工具，让模型具备不同能力组合。
        super().__init__(
            name,
            is_termination_msg=is_termination_msg,
            max_consecutive_auto_reply=max_consecutive_auto_reply,
            human_input_mode=human_input_mode,
            code_execution_config=code_execution_config,
            llm_config=llm_config,
            description=description,
            mimic_human_return=mimic_human_return,
            **kwargs,
        )

        if system_message is not None:
            self.update_system_message(system_message)

        if mode in ["hybrid", "coact_opensource_sft"]:
            # 混合模式：允许 LLM 同时调用 GUI 和编程能力。
            self.update_tool_signature(self.CALL_CODING_AGENT_TOOL, is_remove=False)
            self.update_tool_signature(self.CALL_GUI_AGENT_TOOL, is_remove=False)
        elif mode == "coact_cua_only":
            # 仅 GUI 模式：适合需要视觉/桌面操作的任务。
            self.update_tool_signature(self.CALL_GUI_AGENT_TOOL, is_remove=False)
        elif mode == "coact_coding_only":
            # 仅编程模式：适合只需要代码执行和文件编辑的场景。
            self.update_tool_signature(self.CALL_CODING_AGENT_TOOL, is_remove=False)



class OrchestratorUserProxyAgent(MultimodalConversableAgent):
    """真正执行任务的“代理代理”。

    它承担三件关键事：
    1. 对外暴露工具函数（例如 call_gui_operator / call_programmer）；
    2. 维护一个桌面环境，允许 Agent 进行屏幕操作或代码执行；
    3. 在任务完成后，把中间过程整理成可读结果返回给上一层。

    这个类更接近运行时执行器，而不是纯粹的推理器：
    它会实际连接 DesktopEnv、启动 GUI/编程子代理，并收集执行结果。
    """

    DEFAULT_AUTO_REPLY = "Please continue the task. Note that the user's task is: {user_instruction}. If everything is done, please reply me only with 'TERMINATE'. If the task is impossible to solve, please reply me only with 'INFEASIBLE'."

    def __init__(
        self,
        name: str,
        is_termination_msg: Optional[Callable[[dict[str, Any]], bool]] = None,
        max_consecutive_auto_reply: Optional[int] = None,
        human_input_mode: Optional[str] = "NEVER",
        code_execution_config: Optional[Union[dict[str, Any], Literal[False]]] = {},
        default_auto_reply: Optional[Union[str, dict[str, Any]]] = DEFAULT_AUTO_REPLY,
        llm_config: Optional[Union[LLMConfig, dict[str, Any], Literal[False]]] = False,
        system_message: Optional[Union[str, list]] = "",
        description: Optional[str] = None,

        # GUI Agent config
        provider_name: str = "docker",
        path_to_vm: str = None,
        observation_type: str = "screenshot",
        screen_width: int = 1920,
        screen_height: int = 1080,
        sleep_after_execution: float = 1.0,
        truncate_history_inputs: int = 51,
        cua_max_steps: int = 50,
        coding_max_steps: int = 30,
        history_save_dir: str = "",
        coding_model: str = "o4-mini",
        summarizer_model: str = "o3",
        llm_config_path: str = "",
        region: str = "",
        client_password: str = "",
        user_instruction: str = "",
        cua_client_config: dict = {},
        cua_model: str = "computer-use-preview",
        video_reflection: bool = False,
        genai_client: Optional[genai.Client] = None,
        mimic_human_return: bool = True,
        remote_ip_port: str = None,
    ):
        description = description if description is not None else ""
        # 一个 Agent 可以“说话”和“调用工具”，但它需要先有统一的对话基类能力。
        # 这里把用户任务作为默认自动回复的一部分，确保对话在任务没有明确终止时仍能继续推进。
        super().__init__(
            name=name,
            system_message=system_message,
            mimic_human_return=mimic_human_return,
            is_termination_msg=is_termination_msg,
            max_consecutive_auto_reply=max_consecutive_auto_reply,
            human_input_mode=human_input_mode,
            code_execution_config=code_execution_config,
            llm_config=llm_config,
            default_auto_reply=default_auto_reply.format(user_instruction=user_instruction),
            description=description,
        )
        # 这里把两个工具注册成 Agent 可调用的函数。
        # LLM 看到这些工具名后，可以决定“我现在应该让 GUI 去点按钮，还是让程序员去执行代码”。
        self.register_function(
            function_map={
                "call_gui_operator": lambda **args: self._call_gui_operator(**args, screen_width=screen_width, screen_height=screen_height),
                "call_programmer": lambda **args: self._call_programmer(**args),
            }
        )
        self._code_execution_config = code_execution_config
        self.cua_config = {
            "max_steps": cua_max_steps,
            "sleep_after_execution": sleep_after_execution,
            "truncate_history_inputs": truncate_history_inputs,
        }
        self.cua_client_config = cua_client_config
        self.region = region
        self.client_password = client_password
        self.task_start_time = 0.0
        # DesktopEnv 是最核心的运行环境，类似一个可控的虚拟桌面：
        # - 可以获取截图；
        # - 可以模拟鼠标/键盘操作；
        # - 可以在容器/VM 中执行命令。
        self.use_remote_env = True if remote_ip_port is not None else False
        if self.use_remote_env:
            provider_config = ProviderConfig(
                host=remote_ip_port.split(":")[0],
                port=int(remote_ip_port.split(":")[1]),
            )

            self.env = DesktopEnv(
                provider_name="docker_remote_fc_v1",
                provider_config=provider_config,
                action_space="pyautogui",
                os_type="Ubuntu",
                region=region,
                snapshot_name="init_state",
                screen_size=(screen_width, screen_height),
                headless=True,
                enable_proxy=True,
                require_a11y_tree=observation_type in ["a11y_tree", "screenshot_a11y_tree", "som"],
            )
        else:
            self.env = DesktopEnv(
                path_to_vm=path_to_vm,
                action_space="pyautogui",
                provider_name=provider_name,
                os_type="Ubuntu",
                region=region,
                snapshot_name="init_state",
                screen_size=(screen_width, screen_height),
                headless=True,
                enable_proxy=True,
                require_a11y_tree=observation_type in ["a11y_tree", "screenshot_a11y_tree", "som"],
            )

        self.history_save_dir = history_save_dir
        self.cua_call_count = 0
        self.coding_call_count = 0
        self.coding_max_steps = coding_max_steps
        self.coding_model_config = LLMConfig.from_json(path=llm_config_path).where(model=coding_model)
        self.summarizer_model_config = LLMConfig.from_json(path=llm_config_path).where(model=summarizer_model)
        self.cua_model = cua_model

        if video_reflection:
            self.genai_client = genai_client
            if self.genai_client is None:
                self.genai_client = genai.Client(
                    vertexai=True, project='salesforce-research-internal', location='us-west1'
                )
        else:
            self.genai_client = None

    def set_task_start_time(self, task_start_time: float):
        self.task_start_time = task_start_time

    def reset(self, task_config: dict[str, Any], sleep_time: int = 20):
        # 每次新的任务开始前，都要把虚拟桌面恢复到初始状态，避免前一个任务留下污染。
        if self.use_remote_env:
            obs = self.env.reset_docker_remote_fc_v1(task_config=task_config, sleep_time=sleep_time)
        else:
            obs = self.env.reset(task_config=task_config, sleep_time=sleep_time)
        print(f"VM started on localhost:{self.env.vnc_port}", flush=True)
        print(f"Screen size: {self.env.controller.get_vm_screen_size()}", flush=True)
        return obs

    def _call_gui_operator(self, task: str, screen_width: int = 1920, screen_height: int = 1080) -> str:
        """使用 GUI Agent 完成桌面操作类任务。

        这里的思路是：
        - 把当前子任务写入磁盘日志；
        - 选择对应的 GUI 模型实现（OpenAI/Claude/UI-TARS/OpenCUA 等）；
        - 让这个模型在虚拟桌面上执行点击、输入、滚动等动作；
        - 最后把结果和截图返回，供上层继续决策。
        """
        cua_path = os.path.join(self.history_save_dir, f'cua_output_{self.cua_call_count}')
        screen_size = self.env.controller.get_vm_screen_size()
        width = screen_size["width"]
        height = screen_size["height"]
        if not os.path.exists(cua_path):
            os.makedirs(cua_path)
        with open(os.path.join(cua_path, "subtask.txt"), "w") as f:
            f.write(task)
        
        cua_function = None
        if self.cua_model == "computer-use-preview":
            # OpenAI 视觉/桌面操作模型
            from .cua_agent.openai_cua_agent import run_openai_cua

            cua_function = run_openai_cua
        elif 'claude' in self.cua_model and 'anthropic' not in self.cua_model:
            from .cua_agent.claude_cua_agent import run_claude_cua

            cua_function = run_claude_cua
        elif 'anthropic' in self.cua_model:
            from .cua_agent.claude_cua_agent_bedrock import run_claude_cua_bedrock

            cua_function = run_claude_cua_bedrock
        elif 'UI-TARS-1.5' in self.cua_model:
            from .cua_agent.uitars_cua_agent import run_uitars_cua

            cua_function = run_uitars_cua
        elif 'OpenCUA' in self.cua_model:
            from .cua_agent.opencua_cua_agent import run_opencua_cua

            cua_function = run_opencua_cua
        elif self.cua_model.startswith("gui-plus"):
            from .cua_agent.qwen_gui_cua_agent import run_qwen_gui_cua

            cua_function = run_qwen_gui_cua

        if cua_function is None:
            return f"# Unsupported GUI Operator model: {self.cua_model}"

        try:
            history_inputs, result, cost = cua_function(self.env,
                                                        task,
                                                        save_path=cua_path,
                                                        screen_width=width,
                                                        screen_height=height,
                                                        client_password=self.client_password,
                                                        cua_client_config=self.cua_client_config,
                                                        cua_model=self.cua_model,
                                                        **self.cua_config)
            screenshot = self.env.controller.get_screenshot()
            with open(os.path.join(cua_path, "history_inputs.json"), "w") as f:
                json.dump(history_inputs, f)
            with open(os.path.join(cua_path, "result.txt"), "w") as f:
                f.write(result)
            with open(os.path.join(cua_path, "cost.txt"), "w") as f:
                f.write(str(cost))

            self.cua_call_count += 1

        except Exception:
            # 任何一次 GUI 调用失败，都不会直接让程序崩掉，而是记录详细 traceback 给上层。
            return f"# Call GUI operator error: {traceback.format_exc()}"

        if "TERMINATE" in result:
            result = result.replace("TERMINATE", "").strip()
        elif "UNEXPECTED" in result:
            result = result.strip()
        else:
            result = f"OTHER: {result}"
        return f"# Response from the GUI operator: \n{result}\n<img data:image/png;base64,{base64.b64encode(screenshot).decode('utf-8')}>"
    
    def _call_programmer(self, task: str) -> str:
        """使用编程 Agent 完成代码类任务。

        它的执行链路大致是：
        1. 读取当前屏幕截图；
        2. 启动一个 Coding Agent 和一个终端执行器；
        3. 让 Agent 生成并运行代码；
        4. 对话历史中提取关键摘要，避免长日志直接喧宾夺主；
        5. 返回给上层一个简洁、可读的结论。
        """
        default_auto_reply = "I'm a code interpreter and I can only execute your code or end the conversation. Did you check your result carefully and make sure the things out of the user's instruction are not changed? If you think the task completed, please reply me only with 'TERMINATE'."
        try:
            # 当前桌面截图会作为上下文发给编程代理，帮助它理解可视化界面状态。
            screenshot = self.env.controller.get_screenshot()
            coding_agent = MultimodalConversableAgent(
                name="coding_agent",
                llm_config=self.coding_model_config,
                system_message=CODER_SYSTEM_MESSAGE.format(CLIENT_PASSWORD=self.client_password),
            )
            # TerminalProxyAgent 才是真正执行命令的组件：
            # 它会在虚拟环境里运行脚本、观察输出，并在合适时机终止对话。
            code_interpreter = TerminalProxyAgent(
                name="code_interpreter",
                human_input_mode="NEVER",
                code_execution_config={
                    "use_docker": False,
                    "timeout": 300,
                    "last_n_messages": 1,
                },
                max_consecutive_auto_reply = None,
                default_auto_reply = default_auto_reply,
                description = None,
                is_termination_msg=lambda x: x.get("content", "") and x.get("content", "")[0]["text"].lower() == "terminate",
                env=self.env,
            )
            coding_agent.update_system_message(CODER_SYSTEM_MESSAGE.format(CLIENT_PASSWORD=self.client_password))

            code_interpreter.initiate_chat(
                recipient=coding_agent,
                message=f"# Task\n{task}\n\n<img data:image/png;base64,{base64.b64encode(screenshot).decode('utf-8')}>",
                max_turns=self.coding_max_steps,
            )
        
            chat_history = []
            key = list(code_interpreter.chat_messages.keys())[0]
            chat_messages = code_interpreter.chat_messages[key]
            for item in chat_messages:
                for content in item['content']:
                    if content['type'] == 'image_url':
                        content['image_url']['url'] = '<image>'
                chat_history.append(item)
            
            if not os.path.exists(os.path.join(self.history_save_dir, f'coding_output_{self.coding_call_count}')):
                os.makedirs(os.path.join(self.history_save_dir, f'coding_output_{self.coding_call_count}'))
                
            with open(os.path.join(self.history_save_dir, f'coding_output_{self.coding_call_count}', "chat_history.json"), "w") as f:
                json.dump(chat_history, f)
            with open(os.path.join(self.history_save_dir, f'coding_output_{self.coding_call_count}', f'coding_agent_system_prompt.txt'), "w") as f:
                f.write(CODER_SYSTEM_MESSAGE)
            with open(os.path.join(self.history_save_dir, f'coding_output_{self.coding_call_count}', f'subtask.txt'), "w") as f:
                f.write(task)
            self.coding_call_count += 1

            # Review the group chat history
            # 代码执行过程可能很长，因此这里使用一个小型 summarizer 把对话整理成更短的结论。
            summarizer = ConversableAgent(
                name="summarizer",
                llm_config=self.summarizer_model_config,
                system_message="",
            )
            summarized_history = summarizer.generate_oai_reply(
                messages=[
                    {
                        "role": "user",
                        "content": CONVERSATION_REVIEW_PROMPT.format(task=task, chat_history=chat_history),
                    }
                ]
            )[1]
        except Exception:
            return f"# Call programmer error: {traceback.format_exc()}"

        return f"# Response from the programmer: {summarized_history}"
