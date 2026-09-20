# CoAct-1 Experiment Guide

## System Overview
CoAct-1 is a computer-using agent implementing "Coding as Actions" (paper: *CoAct-1: Computer-using Agents with Coding as Actions*). An **Orchestrator** plans each task and dispatches to two executors: a **GUI Operator** (screen clicks/keys) and a **Coding Agent / Programmer** (Python & Bash). Tasks run on an Ubuntu VM in Docker; supported apps include VS Code, Chrome, GIMP, LibreOffice, Thunderbird, VLC, plus OS-level operations.

Run modes (`--mode`): `hybrid` (default), `coact_cua_only`, `coact_coding_only` (also `human`, `coact_opensource_sft`; not used for Qwen experiments).

## Environment
- **Repo**: `/data1/gjy/CoAct-1`
- **VM image**: `/data1/gjy/coact-runtime/vm/Ubuntu.qcow2`
- **Conda env**: `coact` (`conda activate coact`)
- **Task sets**: `evaluation_examples/*.json` — `mini_coding_one.json` (single task, smoke), `mini_coding.json` (small batch), `test_all.json` / `test_small.json` (full/small OSWorld sets), `infeasible_tasks.json`
- **Task configs**: `evaluation_examples/examples/` (pass via `--test_config_base_dir`)

## Model Configuration (Qwen)
The config file is gitignored — create it once per machine:
```bash
cp OAI_CONFIG_LIST_QWEN.example OAI_CONFIG_LIST_QWEN   # then fill in the real DashScope key
```

| Model | Roles | Selected via |
|---|---|---|
| `qwen3.8-flash` | orchestrator / coding / summarizer | `--orchestrator_model`, `--coding_model`, `--summarizer_model` |
| `gui-plus-2026-02-26` | GUI operator | `--cua_model` |

All API keys are read from this file (`--oai_config_path`). The GUI-Plus entry is matched by model name and wired into the GUI agent automatically (`OrchestratorUserProxyAgent.__init__`); it falls back to the `DASHSCOPE_API_KEY` env var only if the entry is missing or its key is an unfilled placeholder.

## Running Experiments

### Coding Only
```bash
python run_coact.py \
  --provider_name docker \
  --path_to_vm /data1/gjy/coact-runtime/vm/Ubuntu.qcow2 \
  --mode coact_coding_only \
  --oai_config_path OAI_CONFIG_LIST_QWEN \
  --orchestrator_model qwen3.8-flash \
  --coding_model qwen3.8-flash \
  --summarizer_model qwen3.8-flash \
  --test_all_meta_path evaluation_examples/mini_coding_one.json \
  --test_config_base_dir evaluation_examples/examples \
  --num_envs 1 \
  --orchestrator_max_steps 8 \
  --coding_max_steps 10 \
  --result_dir ./results_qwen_coding_smoke \
  --log_level INFO
```

### GUI Only
Same as above with `--mode coact_cua_only`, `--cua_model gui-plus-2026-02-26`, `--cua_max_steps 25` (instead of the coding model/steps args).

### Hybrid
`--mode hybrid` plus both the coding model args and `--cua_model gui-plus-2026-02-26`.

### Operational notes
- **Step-limit defaults** (if flags omitted): orchestrator 15, coding 20, cua 25.
- **Parallelism**: worker count = `min(cpu_count() // 2, --num_envs)`; each task gets its own VM process.
- **Long runs must survive SSH disconnects** (a dropped session once killed a run mid-step):
  ```bash
  setsid nohup python run_coact.py ... > logs/run.out 2>&1 < /dev/null &
  ```
- **After interrupted runs**, clean dead VM containers (they pin RAM/ports/volumes):
  `scripts/sweep_stale_vm_containers.sh [--dry-run] /data1/gjy/coact-runtime/vm/Ubuntu.qcow2`
- `--result_dir` must not already exist for a fresh run; scores are aggregated per domain and printed at the end.

## Outputs
- **Results**: `{result_dir}/coact_{mode}/{domain}/{ex_id}/`
  - `result.txt` — score/verdict (also used for the final aggregation)
  - `chat_history.json` — orchestrator conversation trace
  - `initial_screenshot_orchestrator.png`, `coding_output_N/` (per-call subtask, coding agent history, system prompt)
  - Mode-level: `{result_dir}/coact_{mode}/orchestrator_system_prompt.txt`
- **Logs**: `logs/normal-{timestamp}.log` (INFO) and `logs/debug-{timestamp}.log` (DEBUG), created automatically per run.

## Key Code Paths
- Entry: `run_coact.py` (`process_task` → `OrchestratorAgent` + `OrchestratorUserProxyAgent`)
- Orchestrator/GUI dispatch: `mm_agents/coact/orchestrator_agent.py` (`_call_gui_operator`, `_call_programmer`; GUI-Plus branch at `cua_model.startswith("gui-plus")`)
- GUI-Plus adapter: `mm_agents/coact/cua_agent/qwen_gui_cua_agent.py` (+ `gui_plus_prompt.py`); unit tests: `tests/test_qwen_gui_adapter.py`
