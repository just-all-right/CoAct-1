from pathlib import Path
from urllib.parse import unquote
import json
import shutil
import uuid


ROOT = Path("/data1/gjy/CoAct-1")

ASSET_ROOT = ROOT / "assets" / "ubuntu_osworld_file_cache"
CACHE_ROOT = ROOT / "cache"
EXAMPLE_ROOT = ROOT / "evaluation_examples" / "examples"

HF_PREFIX = (
    "https://huggingface.co/datasets/"
    "xlangai/ubuntu_osworld_file_cache/resolve/main/"
)

CACHE_ROOT.mkdir(parents=True, exist_ok=True)


def local_path_from_url(url: str):
    if not isinstance(url, str):
        return None

    if HF_PREFIX not in url:
        return None

    relative = url.split(HF_PREFIX, 1)[1]
    relative = relative.split("?", 1)[0]
    relative = unquote(relative)

    return ASSET_ROOT / relative


def copy_file(src: Path, dst: Path):
    if not src.exists():
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)

    if not dst.exists():
        shutil.copy2(src, dst)

    return True


def preseed_setup_downloads(task):
    """
    SetupController expects:

        cache/<task_id>/<uuid5(url)>_<basename(vm_path)>
    """

    task_id = task["id"]
    task_cache = CACHE_ROOT / task_id
    task_cache.mkdir(parents=True, exist_ok=True)

    copied = 0
    missing = 0

    for cfg in task.get("config", []):
        if cfg.get("type") != "download":
            continue

        parameters = cfg.get("parameters", {})

        for f in parameters.get("files", []):
            url = f.get("url")
            vm_path = f.get("path")

            if not url or not vm_path:
                continue

            src = local_path_from_url(url)

            if src is None:
                continue

            cache_name = (
                f"{uuid.uuid5(uuid.NAMESPACE_URL, url)}_"
                f"{Path(vm_path).name}"
            )

            dst = task_cache / cache_name

            if copy_file(src, dst):
                copied += 1
            else:
                print(f"[MISSING setup] {src}")
                missing += 1

    return copied, missing


def find_cloud_files(obj):
    if isinstance(obj, dict):
        if obj.get("type") == "cloud_file":
            yield obj

        for value in obj.values():
            yield from find_cloud_files(value)

    elif isinstance(obj, list):
        for value in obj:
            yield from find_cloud_files(value)


def preseed_evaluator_files(task):
    """
    Evaluator's get_cloud_file() uses:

        env.cache_dir/<dest>

    and env.cache_dir is also:

        cache/<task_id>
    """

    task_id = task["id"]
    task_cache = CACHE_ROOT / task_id
    task_cache.mkdir(parents=True, exist_ok=True)

    copied = 0
    missing = 0

    evaluator = task.get("evaluator", {})

    for cfg in find_cloud_files(evaluator):
        paths = cfg.get("path")
        dests = cfg.get("dest")

        if not isinstance(paths, list):
            paths = [paths]

        if not isinstance(dests, list):
            dests = [dests]

        for url, dest in zip(paths, dests):
            if not url or not dest:
                continue

            src = local_path_from_url(url)

            if src is None:
                continue

            dst = task_cache / dest

            if copy_file(src, dst):
                copied += 1
            else:
                print(f"[MISSING evaluator] {src}")
                missing += 1

    return copied, missing


def preseed_special_files(task):
    """
    Some setup functions use fixed cache filenames.
    Browse-history setup is one example.
    """

    task_id = task["id"]
    task_cache = CACHE_ROOT / task_id

    config_types = {
        cfg.get("type")
        for cfg in task.get("config", [])
        if isinstance(cfg, dict)
    }

    if "update_browse_history" in config_types:
        src = (
            ASSET_ROOT
            / "chrome"
            / "44ee5668-ecd5-4366-a6ce-c1c9b8d4e938"
            / "history_empty.sqlite"
        )

        dst = task_cache / "history_new.sqlite"

        if src.exists():
            copy_file(src, dst)


def main():
    if not ASSET_ROOT.exists():
        raise SystemExit(
            f"Asset directory does not exist:\n{ASSET_ROOT}"
        )

    if not EXAMPLE_ROOT.exists():
        raise SystemExit(
            f"Evaluation example directory does not exist:\n{EXAMPLE_ROOT}"
        )

    task_files = list(EXAMPLE_ROOT.rglob("*.json"))

    print(f"Found {len(task_files)} task configs")
    print(f"Assets: {ASSET_ROOT}")
    print(f"Cache:  {CACHE_ROOT}")
    print()

    setup_copied = 0
    setup_missing = 0
    eval_copied = 0
    eval_missing = 0

    for json_file in task_files:
        try:
            with json_file.open("r", encoding="utf-8") as f:
                task = json.load(f)
        except Exception as e:
            print(f"[ERROR JSON] {json_file}: {e}")
            continue

        if "id" not in task:
            print(f"[NO TASK ID] {json_file}")
            continue

        c, m = preseed_setup_downloads(task)
        setup_copied += c
        setup_missing += m

        c, m = preseed_evaluator_files(task)
        eval_copied += c
        eval_missing += m

        preseed_special_files(task)

    print()
    print("=== Finished ===")
    print(f"Setup files copied:      {setup_copied}")
    print(f"Setup files missing:     {setup_missing}")
    print(f"Evaluator files copied:  {eval_copied}")
    print(f"Evaluator files missing: {eval_missing}")
    print()
    print(f"CoAct cache directory: {CACHE_ROOT}")


if __name__ == "__main__":
    main()
