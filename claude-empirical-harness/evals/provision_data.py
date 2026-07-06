#!/usr/bin/env python3
"""
One-time data provisioning for eval tasks.

Downloads and extracts data items declared in a task YAML's `data_provision`
section. Supports two modes:

1. Direct items — URLs listed in the task YAML itself:
       data_provision:
         workspace_link: data/raw
         items:
           - url: https://example.com/data.zip
             filename: data.zip      # optional, derived from URL if omitted
             extract: true           # optional, default false
             sha256: abc123...       # optional integrity check

2. Econbench benchmark — delegates to econbench.data:
       data_provision:
         econbench_benchmark: /path/to/benchmark.yaml
         workspace_link: data

Both modes are idempotent: items with a .complete marker are skipped.

Usage:
    python3 evals/provision_data.py --task evals/tasks/scf_debt_age_income.yaml
    python3 evals/provision_data.py --task evals/tasks/scf_debt_age_income.yaml --force

Prints the absolute path to the staged data directory on the last line.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote
from urllib.request import Request, urlopen

import yaml

CHUNK_SIZE = 1024 * 1024
MARKER = ".provision_complete"


def download_file(url: str, dest: Path, sha256: str | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    request = Request(url, headers={"User-Agent": "econbench-harness/0.1"})
    with urlopen(request, timeout=120) as response, part.open("wb") as out:
        total = response.headers.get("Content-Length")
        total = int(total) if total else None
        received = 0
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            out.write(chunk)
            received += len(chunk)
            if total:
                print(f"\r  {received / 1e6:,.1f} / {total / 1e6:,.1f} MB", end="", flush=True)
            else:
                print(f"\r  {received / 1e6:,.1f} MB", end="", flush=True)
        print()

    if total and received != total:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Incomplete download: expected {total} bytes, got {received}")

    if sha256:
        h = hashlib.sha256()
        with part.open("rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        if h.hexdigest().lower() != sha256.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(f"SHA256 mismatch: expected {sha256}, got {h.hexdigest()}")

    part.replace(dest)


def provision_item(
    item: dict, data_dir: Path, force: bool
) -> str:
    url = item["url"]
    filename = item.get("filename") or unquote(Path(url.split("?")[0]).name)
    extract = item.get("extract", False)
    sha256 = item.get("sha256")

    dest = data_dir / filename
    marker = data_dir / MARKER

    if marker.exists() and dest.exists() and not force:
        return "cached"

    if not dest.exists() or force:
        print(f"  downloading {url}")
        download_file(url, dest, sha256=sha256)
    else:
        print(f"  using cached {dest.name}")

    if extract:
        if zipfile.is_zipfile(dest):
            print(f"  extracting {dest.name}")
            with zipfile.ZipFile(dest) as zf:
                try:
                    zf.extractall(data_dir, filter="data")
                except TypeError:
                    zf.extractall(data_dir)
        else:
            raise RuntimeError(f"extract: true but {dest.name} is not a valid zip file")

    marker.write_text(f"source: {url}\n", encoding="utf-8")
    return "provisioned"


def provision_direct(task: dict, task_dir: Path, force: bool) -> Path:
    dp = task.get("data_provision", {})
    items = dp.get("items", [])
    workspace_link = dp.get("workspace_link", "data/raw")

    # Stage data in a persistent cache dir next to the task file
    data_dir = task_dir / ".data_cache" / task["name"]
    data_dir.mkdir(parents=True, exist_ok=True)

    print(f"Provisioning {len(items)} item(s) into {data_dir}")
    failures = []
    for item in items:
        try:
            status = provision_item(item, data_dir, force)
            print(f"  [{item.get('filename', item['url'])}] {status}")
        except Exception as exc:
            failures.append(str(exc))
            print(f"  FAILED: {exc}")

    if failures:
        print(f"\n{len(failures)} item(s) failed.", file=sys.stderr)
        sys.exit(1)

    print(f"All data ready under {data_dir}")
    return data_dir


def provision_econbench(benchmark_path: str, task: dict) -> Path:
    benchmark_path = Path(benchmark_path)
    candidate = benchmark_path.parent.resolve()
    econbench_root = None
    while candidate != candidate.parent:
        if (candidate / "econbench").is_dir() or (candidate / "pyproject.toml").exists():
            econbench_root = candidate
            break
        candidate = candidate.parent

    if not econbench_root:
        print(f"ERROR: could not locate econbench root above {benchmark_path}", file=sys.stderr)
        sys.exit(1)

    sys.path.insert(0, str(econbench_root))
    from econbench.data import provision, resolve_data_dir, load_benchmark

    provision(benchmark_path)
    benchmark, benchmark_dir = load_benchmark(benchmark_path)
    return resolve_data_dir(benchmark, benchmark_dir)


def provision_task(task_file: str, force: bool = False) -> tuple[Path, str]:
    """Returns (data_dir, workspace_link) or exits if nothing to provision."""
    task_path = Path(task_file).resolve()
    with open(task_path) as f:
        task = yaml.safe_load(f)

    dp = task.get("data_provision", {})
    if not dp:
        return Path(""), ""

    workspace_link = dp.get("workspace_link", "data/raw")

    if "econbench_benchmark" in dp:
        data_dir = provision_econbench(dp["econbench_benchmark"], task)
    elif "items" in dp:
        data_dir = provision_direct(task, task_path.parent, force)
    else:
        return Path(""), ""

    return data_dir.resolve(), workspace_link


def main():
    parser = argparse.ArgumentParser(description="Provision data for an eval task.")
    parser.add_argument("--task", required=True, help="Path to task YAML.")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached.")
    args = parser.parse_args()

    data_dir, workspace_link = provision_task(args.task, force=args.force)
    if data_dir and str(data_dir):
        print(f"\nDATA_DIR={data_dir}")
        print(f"WORKSPACE_LINK={workspace_link}")
    else:
        print("No data provisioning configured for this task.")


if __name__ == "__main__":
    main()
