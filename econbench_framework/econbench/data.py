"""
econbench.data

One-time data provisioning for benchmarks.

Raw data acquisition is deliberately separated from agent runs and scoring:
you run this once per benchmark, and every subsequent agent run reads the
pre-staged local copy instead of re-downloading.

The data requirements are declared in the benchmark YAML under a `data:` key:

    data:
      directory: data              # relative to the benchmark folder (default)
      items:
        ahs_1985_national:
          url: https://.../AHS%201985%20National%20PUF%20v2.0%20CSV.zip
          extract: true

Typical usage:

    python -m econbench.data --benchmark benchmarks/wallace/benchmark.yaml
    python -m econbench.data --benchmark benchmarks/wallace/benchmark.yaml --force

Each item is downloaded to <data_dir>/downloads/ and (if `extract: true`)
unpacked to <data_dir>/<item_name>/. A `.econbench_complete` marker file makes
the step idempotent: items that already completed are skipped unless --force
is given. Interrupted downloads are written to a .part file first, so a
partial file is never mistaken for a finished one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.request import urlopen, Request
import argparse
import hashlib
import shutil
import sys
import zipfile

import yaml

MARKER_NAME = ".econbench_complete"
CHUNK_SIZE = 1024 * 1024


def load_benchmark(benchmark_path: str | Path) -> tuple[dict[str, Any], Path]:
    benchmark_path = Path(benchmark_path)
    with benchmark_path.open("r", encoding="utf-8") as f:
        benchmark = yaml.safe_load(f)
    return benchmark, benchmark_path.parent


def resolve_data_dir(benchmark: dict[str, Any], benchmark_dir: Path) -> Path:
    directory = benchmark.get("data", {}).get("directory", "data")
    path = Path(directory)
    return path if path.is_absolute() else benchmark_dir / path


def download_file(url: str, dest: Path, sha256: str | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    request = Request(url, headers={"User-Agent": "econbench-data/0.1"})
    with urlopen(request, timeout=60) as response, part.open("wb") as out:
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
                print(f"\r  {received / 1e6:,.0f} / {total / 1e6:,.0f} MB", end="", flush=True)
            else:
                print(f"\r  {received / 1e6:,.0f} MB", end="", flush=True)
        print()

    if total is not None and received != total:
        part.unlink(missing_ok=True)
        raise RuntimeError(
            f"Incomplete download from {url}: received {received} of {total} bytes"
        )

    if sha256 is not None:
        digest = hashlib.sha256()
        with part.open("rb") as f:
            for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != sha256.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA256 mismatch for {url}: expected {sha256}, got {digest.hexdigest()}"
            )

    part.replace(dest)


def provision_item(name: str, spec: dict[str, Any], data_dir: Path, force: bool) -> str:
    item_dir = data_dir / name
    marker = item_dir / MARKER_NAME

    if marker.exists() and not force:
        return "cached"

    if force and item_dir.exists():
        shutil.rmtree(item_dir)

    url = spec["url"]
    filename = spec.get("filename") or unquote(Path(url.split("?")[0]).name)
    archive_path = data_dir / "downloads" / filename

    if not archive_path.exists() or force:
        print(f"  downloading {url}")
        download_file(url, archive_path, sha256=spec.get("sha256"))
    else:
        print(f"  using previously downloaded {archive_path.name}")

    item_dir.mkdir(parents=True, exist_ok=True)

    if spec.get("extract", True):
        if not zipfile.is_zipfile(archive_path):
            raise RuntimeError(
                f"extract requested for {name} but {archive_path} is not a valid zip file"
            )
        print(f"  extracting to {item_dir}")
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(item_dir, filter="data")
    else:
        shutil.copy2(archive_path, item_dir / filename)

    marker.write_text(f"source: {url}\n", encoding="utf-8")
    return "provisioned"


def provision(benchmark_path: str | Path, force: bool = False) -> Path:
    benchmark, benchmark_dir = load_benchmark(benchmark_path)
    items = benchmark.get("data", {}).get("items", {})
    if not items:
        print("Benchmark declares no data items; nothing to do.")
        return resolve_data_dir(benchmark, benchmark_dir)

    data_dir = resolve_data_dir(benchmark, benchmark_dir)
    print(f"Provisioning {len(items)} data item(s) into {data_dir}")

    failures = []
    for name, spec in items.items():
        print(f"[{name}]")
        try:
            status = provision_item(name, spec, data_dir, force)
            print(f"  {status}")
        except Exception as exc:
            failures.append(name)
            print(f"  FAILED: {exc}")

    if failures:
        print(f"\n{len(failures)} item(s) failed: {', '.join(failures)}. Re-run to retry.")
        sys.exit(1)

    print(f"\nAll data ready under {data_dir}")
    return data_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and stage benchmark data (one-time step).")
    parser.add_argument("--benchmark", required=True, help="Path to benchmark YAML.")
    parser.add_argument("--force", action="store_true", help="Re-download and re-extract even if cached.")
    args = parser.parse_args()

    provision(args.benchmark, force=args.force)


if __name__ == "__main__":
    main()
