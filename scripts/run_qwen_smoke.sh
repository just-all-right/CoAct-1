#!/usr/bin/env bash
# CoAct-1 Qwen single-task "coding only" smoke test.
#
# Detaches with setsid+nohup so a dropped SSH/VS Code session cannot SIGHUP the run
# (that is what killed results_qwen_coding_smoke2 mid-step with no traceback), and
# captures stdout/stderr to a file so tracebacks survive.
#
# usage: scripts/run_qwen_smoke.sh [result_dir_name]
set -o pipefail

REPO=/data1/gjy/CoAct-1
VM=/data1/gjy/coact-runtime/vm/Ubuntu.qcow2   # COACT_VM is not exported in .bashrc, so set it
RESULT_DIR="${1:-results_qwen_coding_smoke3}"

source /data1/gjy/miniconda3/etc/profile.d/conda.sh
conda activate coact
cd "$REPO" || exit 1

if [ -e "$RESULT_DIR" ]; then
  echo "refusing to reuse an existing result dir: $REPO/$RESULT_DIR" >&2
  echo "pass a different name as \$1" >&2
  exit 1
fi

mkdir -p logs
LOG="logs/${RESULT_DIR}.out"

# Drop dead VM containers from earlier interrupted runs (see sweep_stale_vm_containers.sh
# for what makes this safe) so they do not pin RAM, ports, or their storage volumes.
"$REPO/scripts/sweep_stale_vm_containers.sh" "$VM"

setsid nohup python run_coact.py \
  --provider_name docker \
  --path_to_vm "$VM" \
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
  --result_dir "./$RESULT_DIR" \
  --log_level INFO \
  > "$LOG" 2>&1 < /dev/null &

PID=$!
echo "launched: pid=$PID"
echo "log:      $REPO/$LOG"
echo "results:  $REPO/$RESULT_DIR"
echo "follow:   ssh gjy@10.249.40.105 tail -f $REPO/$LOG"
