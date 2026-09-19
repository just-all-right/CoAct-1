import argparse
import base64
import glob
import datetime
import traceback
import json
import os
import sys
import logging
from multiprocessing import Pool, cpu_count
from functools import partial
from typing import Dict, List
from mm_agents.coact.orchestrator_agent import OrchestratorAgent, OrchestratorUserProxyAgent
from mm_agents.coact.autogen import LLMConfig
from mm_agents.coact.coact_prompt import TASK_DESCRIPTION, TASK_DESCRIPTION_CUA_ONLY, TASK_DESCRIPTION_CODING_ONLY


# ============================================================
# 第 0 章：脚本总览
# ============================================================
# 这是 CoAct 的主入口脚本：
#   1) 读取命令行参数，决定环境、模型和任务集合；
#   2) 遍历 benchmark 任务，分发给多个 worker 并行执行；
#   3) 保存截图、聊天记录和最终分数，方便排查和复盘。
#
# 核心主线：
#   benchmark task JSON
#          |
#          v
#   __main__ 读取元数据 -> 生成 tasks -> Pool 并发调度
#          |
#          v
#   process_task() 处理单个任务
#          |
#          v
#   OrchestratorAgent + OrchestratorUserProxyAgent
#          |
#          v
#   获取截图 -> 发起聊天 -> 代理在桌面环境中执行操作 -> env.evaluate()
#          |
#          v
#   保存 result.txt / chat_history.json / 截图
#
# 关键对象：
# - OrchestratorAgent：上层规划器，决定任务策略和动作方向；
# - OrchestratorUserProxyAgent：运行时桥接器，连接 LLM、桌面环境和任务状态；
# - task_config：单个任务的指令和环境说明；
# - orchestrator_proxy.env：真实执行环境，负责截图、操作、评分；
# - history_save_dir：当前任务的产物目录，方便复盘和排查。

# ============================================================
# 第 1 章：启动配置（命令行参数与运行环境）
# ============================================================
def config() -> argparse.Namespace:
    """解析命令行参数，定义整个评测任务的运行配置。

    这里的参数大致分为 4 类：
    1. 虚拟机 / 环境参数：连接哪个桌面环境、分辨率、等待时间等；
    2. 评测模式：hybrid / coact_cua_only / coact_coding_only 等；
    3. 模型参数：编排器、编码器、总结器等使用的 LLM；
    4. 任务与日志参数：benchmark 数据目录、结果目录、并发数等。
    """
    parser = argparse.ArgumentParser(
        description="Run end-to-end evaluation on the benchmark"
    )

    # environment config
    # 这些参数控制运行任务时所连接的桌面 VM / 容器，并决定屏幕大小和操作间隔。
    parser.add_argument("--path_to_vm", type=str, default="")
    parser.add_argument("--provider_name", type=str, default="docker")
    parser.add_argument("--screen_width", type=int, default=1920)
    parser.add_argument("--screen_height", type=int, default=1080)
    parser.add_argument("--sleep_after_execution", type=float, default=1.0)
    parser.add_argument("--region", type=str, default="us-east-1")
    parser.add_argument("--client_password", type=str, default="password")
    parser.add_argument("--remote_ip_port", type=str, default=None)

    # agent config
    # mode 决定代理的能力组合：混合模式/纯 GUI / 纯编程等。
    parser.add_argument("--mode", type=str, default="hybrid", choices=["human", "hybrid", "coact_cua_only", "coact_coding_only", "coact_opensource_sft"])
    parser.add_argument("--oai_config_path", type=str, default="OAI_CONFIG_LIST_sfr")
    parser.add_argument("--orchestrator_model", type=str, default="Qwen/Qwen3-VL-32B-Instruct")
    parser.add_argument("--coding_model", type=str, default="gpt-5-mini")
    parser.add_argument("--summarizer_model", type=str, default="gpt-5-mini")

    # GUI Agent:
    # CUA 相关参数控制图形界面代理的行为上限、步数和模型选择。
    parser.add_argument("--cua_model", type=str, default="ByteDance-Seed/UI-TARS-1.5-7B")
    parser.add_argument("--orchestrator_max_steps", type=int, default=15)
    parser.add_argument("--coding_max_steps", type=int, default=20)
    parser.add_argument("--cua_max_steps", type=int, default=25)
    parser.add_argument("--cut_off_steps", type=int, default=150)

    # example config
    # benchmark 任务集位于 evaluation_examples 下，domain 表示具体任务域。
    parser.add_argument("--domain", type=str, default="all")
    parser.add_argument(
        "--test_all_meta_path", type=str, default="evaluation_examples/test_nogdrive.json"
    )
    parser.add_argument(
        "--test_config_base_dir", type=str, default="evaluation_examples/examples"
    )

    # logging related
    # result_dir 用于保存每个任务的截图、聊天记录、最终分数等产物。
    parser.add_argument("--result_dir", type=str, default="./results_coact")
    parser.add_argument("--num_envs", type=int, default=20, help="Number of environments to run in parallel")
    parser.add_argument("--log_level", type=str, choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        default='INFO', help="Set the logging level")

    args = parser.parse_args()

    return args


# ============================================================
# 第 2 章：单任务执行循环
# ============================================================
# 这个函数是“一个 benchmark 任务”的完整执行过程，几乎就是整个框架的核心。
# 它会：
#   1) 读取任务 JSON 配置；
#   2) 初始化 Orchestrator + 桌面环境；
#   3) 让代理基于截图和指令做多轮决策；
#   4) 统计中间动作和最终结果，写入日志；
#   5) 结束时返回该任务的分数。
#
# 如果你想快速理解 CoAct 的主体架构，重点阅读这一段就够了。
def process_task(task_info,
                provider_name,
                path_to_vm,
                mode="coact",
                orchestrator_model="o3",
                coding_model='o4-mini',
                summarizer_model='gpt-5',
                save_dir='results',
                orchestrator_max_steps=15,
                cua_max_steps=25,
                coding_max_steps=20,
                cut_off_steps=150,
                screen_width=1920,
                screen_height=1080,
                sleep_after_execution=1.0,
                config_path="OAI_CONFIG_LIST",
                region="us-east-1",
                client_password="",
                remote_ip_port=None,
                cua_model="computer-use-preview",
                ):
    """处理单个 benchmark 任务，返回该任务的最终分数。

    核心思路：
    - task_info 里包含 domain / ex_id / cfg，分别表示任务领域、任务编号和任务 JSON 配置；
    - 读取任务配置后，实例化 OrchestratorUserProxyAgent，真正与桌面环境交互；
    - 代理会从屏幕截图出发，决定下一步点击、键入、执行命令，直到任务完成或超步数；
    - 最后调用环境内置 evaluate() 对结果评分，并保存任务日志和图片。
    """
    domain, ex_id, cfg = task_info

    # 为每个子进程单独加载 LLM 配置，避免并行执行时共享状态出现冲突。
    llm_config = LLMConfig.from_json(path=config_path).where(model=orchestrator_model)

    # 每个任务都有自己的结果目录，方便后续排查问题、复盘执行轨迹。
    history_save_dir = os.path.join(save_dir, f"coact_{mode}", f"{domain}/{ex_id}")
    if not os.path.exists(history_save_dir):
        os.makedirs(history_save_dir)

    # Benchmark 任务定义通常是 JSON，里面存有 instruction、目标状态等元信息。
    task_config = json.load(open(cfg))

    while True:
        try:
            orchestrator_proxy = None
            mimic_human_return = True
            with llm_config:
                # 根据运行模式选择不同的编排器提示词；这里决定代理如何分配“GUI 操作 / 代码执行 / 规划”职责。
                if mode == "coact_cua_only":
                    orchestrator = OrchestratorAgent(
                        name="orchestrator",
                        system_message=TASK_DESCRIPTION_CUA_ONLY,
                        mode=mode
                    )
                elif mode == "coact_coding_only":
                    orchestrator = OrchestratorAgent(
                        name="orchestrator",
                        system_message=TASK_DESCRIPTION_CODING_ONLY,
                        mode=mode
                    )
                elif mode == "coact_opensource_sft":
                    orchestrator = OrchestratorAgent(
                        name="orchestrator",
                        mode=mode,
                        system_message="Complete the user's task. You have 10 steps in total. Reply with TERMINATE if completed. Try call_programmer first.",
                        mimic_human_return=False
                    )
                    mimic_human_return = False
                else:
                    orchestrator = OrchestratorAgent(
                        name="orchestrator",
                        system_message=TASK_DESCRIPTION,
                        mode=mode
                    )

                # OrchestratorUserProxyAgent 是整个任务的执行入口，负责代理和桌面环境之间的桥接。
                # 它会调度 GUI Agent / coding agent，并持有 env 这个运行时对象。
                orchestrator_proxy = OrchestratorUserProxyAgent(
                    name="orchestrator_proxy",
                    is_termination_msg=lambda x: x.get("content", "") and (x.get("content", "")[0]["text"].lower() == "terminate" or x.get("content", "")[0]["text"].lower() == "infeasible"),
                    human_input_mode="NEVER",
                    provider_name=provider_name,
                    path_to_vm=path_to_vm,
                    screen_width=screen_width,
                    screen_height=screen_height,
                    sleep_after_execution=sleep_after_execution,
                    code_execution_config=False,
                    history_save_dir=history_save_dir,
                    coding_model=coding_model,
                    summarizer_model=summarizer_model,
                    llm_config_path=config_path,
                    truncate_history_inputs=cua_max_steps + 1,
                    cua_max_steps=cua_max_steps,
                    coding_max_steps=coding_max_steps,
                    region=region,
                    client_password=client_password,
                    user_instruction=task_config["instruction"],
                    cua_model=cua_model,
                    # cua_client_config=json.load(open("sf_cua_openai_config.key")),  # remove this line for non-SF usage
                    mimic_human_return=mimic_human_return,
                    remote_ip_port=remote_ip_port
                )

            # 重置代理状态，并拿到当前桌面截图作为任务初始观测。
            orchestrator_proxy.reset(task_config=task_config)
            screenshot = orchestrator_proxy.env.controller.get_screenshot()

            with open(os.path.join(history_save_dir, f'initial_screenshot_orchestrator.png'), "wb") as f:
                f.write(screenshot)

            # 让代理从第一张截图和用户指令开始进行多轮对话，直到任务结束或步数上限。
            orchestrator_proxy.initiate_chat(
                recipient=orchestrator,
                message=f"<img data:image/png;base64,{base64.b64encode(screenshot).decode('utf-8')}>{task_config['instruction']}",
                max_turns=orchestrator_max_steps,
                silent=True
            )

            # 保存聊天历史，剥离一些运行时字段（例如 tool_responses / image_url）以便更易读地复盘。
            chat_history = []
            key = list(orchestrator_proxy.chat_messages.keys())[0]
            chat_messages = orchestrator_proxy.chat_messages[key]
            for item in chat_messages:
                item.pop('tool_responses', None)
                if item.get('role', None) in ['tool', 'assistant'] and item.get('content', None):
                    for msg in item['content']:
                        if msg.get('type', None) == 'image_url':
                            msg['image_url'] = "<image>"
                chat_history.append(item)

            with open(os.path.join(history_save_dir, f'chat_history.json'), "w") as f:
                json.dump(chat_history, f)

            # 如果任务返回的是 infeasible，表示当前环境无法完成该任务，记录失败状态。
            if chat_history[-1]['role'] == 'user' and 'INFEASIBLE' in chat_history[-1]['content'][0]['text']:
                orchestrator_proxy.env.action_history.append("FAIL")

            # 统计 GUI 和 coding 产物的执行步数，用于判断是否超过预算，防止不必要的长尾行为。
            cua_steps = len(glob.glob(f"{history_save_dir}/cua_output*/step_*.png"))
            coding_paths = glob.glob(f"{history_save_dir}/coding_output*/chat_history.json")
            coding_steps = 0
            for hist in coding_paths:
                with open(hist, 'r') as f:
                    hist = json.dumps(json.load(f))
                    coding_steps += hist.count('exitcode:')
            if cua_steps + coding_steps > cut_off_steps:
                score = 0.0
            else:
                score = orchestrator_proxy.env.evaluate()
            print(f"Score: {score}")

            # 结果写入文件，供后续汇总统计和复盘分析使用。
            with open(os.path.join(history_save_dir, f'result.txt'), "w") as f:
                f.write(str(score))
            break
        except Exception as e:
            print(f"Error processing task {domain}/{ex_id}")
            traceback.print_exc()
            score = 0.0
            with open(os.path.join(history_save_dir, f'result.txt'), "w") as f:
                f.write(str(score))
            with open(os.path.join(history_save_dir, f'err_reason.txt'), "w") as f:
                f.write(f"Fatal error: {str(e)}")
            break
        finally:
            try:
                if orchestrator_proxy is not None and getattr(orchestrator_proxy, 'env', None) is not None:
                    orchestrator_proxy.env.close()
            except Exception:
                pass

    return domain, score


# ============================================================
# 第 3 章：主程序入口（任务装载、日志初始化、并发调度）
# ============================================================
# 这里是脚本真正开始执行的地方。它完成的事情可以概括为：
#   - 读取 benchmark 元数据，构造所有待执行任务；
#   - 过滤掉已经跑过的任务，支持断点续跑；
#   - 设定日志输出；
#   - 使用多进程并行处理 tasks；
#   - 汇总每个 domain 的平均分并输出结果。
if __name__ == "__main__":
    # 入口函数：先读取命令行参数，再启动日志和任务调度。
    args = config()

    logger = logging.getLogger()

    log_level = getattr(logging, args.log_level.upper())
    logger.setLevel(log_level)

    # 日志按时间分文件，方便追踪任务运行过程。
    os.makedirs("logs", exist_ok=True)
    datetime_str: str = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
    file_handler = logging.FileHandler(
        os.path.join("logs", "normal-{:}.log".format(datetime_str)), encoding="utf-8"
    )
    debug_handler = logging.FileHandler(
        os.path.join("logs", "debug-{:}.log".format(datetime_str)), encoding="utf-8"
    )
    stdout_handler = logging.StreamHandler(sys.stdout)
    file_handler.setLevel(logging.INFO)
    debug_handler.setLevel(logging.DEBUG)
    stdout_handler.setLevel(log_level)
    formatter = logging.Formatter(
        fmt="\x1b[1;33m[%(asctime)s \x1b[31m%(levelname)s \x1b[32m%(module)s/%(lineno)d-%(processName)s\x1b[1;33m] \x1b[0m%(message)s"
    )
    file_handler.setFormatter(formatter)
    debug_handler.setFormatter(formatter)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(logging.Filter("desktopenv"))
    logger.addHandler(file_handler)
    logger.addHandler(debug_handler)
    logger.addHandler(stdout_handler)
    logger = logging.getLogger("desktopenv.expeiment")

    # 读取 benchmark 元数据，例如有哪些 domain 和任务编号。
    with open(args.test_all_meta_path, encoding="utf-8") as f:
        test_all_meta = json.load(f)
    if args.domain != "all":
        test_all_meta = {args.domain: test_all_meta[args.domain]}
    if not os.path.exists(os.path.join(args.result_dir, f'coact_{args.mode}')):
        os.makedirs(os.path.join(args.result_dir, f'coact_{args.mode}'))

    # 保存当前运行模式的 system prompt，供后续复盘和调试使用。
    with open(os.path.join(args.result_dir, f'coact_{args.mode}', f'orchestrator_system_prompt.txt'), "w") as f:
        f.write(TASK_DESCRIPTION)

    # tasks 是待执行的任务列表；每个元素是 (domain, ex_id, cfg_path) 的三元组。
    tasks = []
    scores: Dict[str, List[float]] = {}
    for domain in test_all_meta:
        scores[domain] = []
        for ex_id in test_all_meta[domain]:
            # 跳过已经算完的任务，避免重复执行。
            if os.path.exists(os.path.join(args.result_dir, f'coact_{args.mode}', f"{domain}/{ex_id}/result.txt")):
                result = open(os.path.join(args.result_dir, f'coact_{args.mode}', f"{domain}/{ex_id}/result.txt"), "r").read()
                print(f"Results already exist in {domain}/{ex_id}, result: {result}")
                continue
            cfg = os.path.join(args.test_config_base_dir, f"{domain}/{ex_id}.json")
            tasks.append((domain, ex_id, cfg))

    # 任务已完成的情况：打印已有结果的汇总信息。这个分支是“断点续跑”的关键入口。
    if not tasks:
        print("No tasks to process. All tasks have already been completed.")
        print("\n=== Summary of Existing Results ===")
        for domain in test_all_meta:
            domain_scores = []
            for ex_id in test_all_meta[domain]:
                score_file = os.path.join(args.result_dir, f'coact_{args.mode}', f"{domain}/{ex_id}/result.txt")
                if os.path.exists(score_file):
                    with open(score_file, "r") as f:
                        domain_scores.append(float(f.read()))
            if domain_scores:
                avg_score = sum(domain_scores) / len(domain_scores)
                print(f"{domain}: {len(domain_scores)} tasks, average score: {avg_score:.2f}")
    else:
        # 采用多进程并行跑 benchmark：每个 task 在单独进程中执行，互不干扰。
        # num_workers 取 CPU 核心数的一半，以平衡并发和机器负载。
        num_workers = min(cpu_count() // 2, args.num_envs)
        print(f"Processing {len(tasks)} tasks with {num_workers} workers...")

        # partial 将参数绑定到 process_task，方便 pool.imap_unordered() 并行调度。
        process_func = partial(process_task,
                                    mode=args.mode,
                                    provider_name=args.provider_name,
                                    path_to_vm=args.path_to_vm,
                                    save_dir=args.result_dir,
                                    coding_model=args.coding_model,
                                    summarizer_model=args.summarizer_model,
                                    orchestrator_model=args.orchestrator_model,
                                    config_path=args.oai_config_path,
                                    orchestrator_max_steps=args.orchestrator_max_steps,
                                    cua_max_steps=args.cua_max_steps,
                                    coding_max_steps=args.coding_max_steps,
                                    cut_off_steps=args.cut_off_steps,
                                    screen_width=args.screen_width,
                                    screen_height=args.screen_height,
                                    sleep_after_execution=args.sleep_after_execution,
                                    region=args.region,
                                    client_password=args.client_password,
                                    remote_ip_port=args.remote_ip_port,
                                    cua_model=args.cua_model)

        # pool.imap_unordered() 按任务完成顺序返回结果，适合大批量评测场景。
        with Pool(processes=num_workers) as pool:
            for domain, score in pool.imap_unordered(process_func, tasks, chunksize=1):
                scores[domain].append(score)

        # 汇总各 domain 的平均分，便于快速看整体表现。
        print("\n=== Task Processing Complete ===")
        for domain, scores in scores.items():
            if scores:
                avg_score = sum(scores) / len(scores)
                print(f"{domain}: {len(scores)} tasks, average score: {avg_score:.2f}")
