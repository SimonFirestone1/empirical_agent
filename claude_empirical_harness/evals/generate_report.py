#!/usr/bin/env python3
"""
Generate an HTML report for a single eval trial.

Task-driven: if --task points to a task YAML with an `expected_outputs:` list,
those files are rendered specifically. Regardless, an "All Outputs" gallery
renders everything found under outputs/ (PNG/JPG/SVG as embedded images, CSVs
as tables, .md/.txt as preformatted text).

Usage:
    python3 evals/generate_report.py --trial-dir evals/results/<task>/<trial> \
        [--task evals/tasks/<task>.yaml]
"""
from __future__ import annotations

import argparse
import base64
import html as html_mod
import json
from pathlib import Path

import pandas as pd

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".svg": "image/svg+xml"}
TEXT_EXTS = {".md", ".txt"}
MAX_TEXT_CHARS = 100_000


def read_meta(trial_dir: Path) -> dict:
    meta_path = trial_dir / "meta.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text())
    return {}


def read_csv_safe(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None


def df_to_html(df: pd.DataFrame, max_rows: int = 200) -> str:
    if len(df) > max_rows:
        df = df.head(max_rows)
    return df.to_html(index=False, classes="data-table", border=0, na_rep="—")


def image_to_html(path: Path, caption: str) -> str:
    mime = IMAGE_EXTS[path.suffix.lower()]
    b64 = base64.b64encode(path.read_bytes()).decode()
    cap = html_mod.escape(caption)
    return (f'<figure><img src="data:{mime};base64,{b64}" alt="{cap}">'
            f"<figcaption>{cap}</figcaption></figure>")


def render_file(path: Path, rel_name: str) -> str:
    """Render a single output file as an HTML fragment (heading + content)."""
    heading = f"<h3><code>{html_mod.escape(rel_name)}</code></h3>"
    suffix = path.suffix.lower()
    try:
        if suffix in IMAGE_EXTS:
            return heading + image_to_html(path, rel_name)
        if suffix == ".csv":
            df = read_csv_safe(path)
            if df is not None:
                return heading + df_to_html(df)
            return heading + '<p class="missing">unreadable CSV</p>'
        if suffix in TEXT_EXTS:
            text = path.read_text(errors="replace")[:MAX_TEXT_CHARS]
            return heading + f'<pre class="memo">{html_mod.escape(text)}</pre>'
        size = path.stat().st_size
        return heading + f'<p class="missing">binary/other file ({size:,} bytes) — not rendered</p>'
    except Exception as exc:
        return heading + f'<p class="missing">failed to render: {html_mod.escape(str(exc))}</p>'


def load_expected_outputs(task_yaml: Path | None) -> list[str]:
    if not task_yaml or not task_yaml.exists() or yaml is None:
        return []
    try:
        task = yaml.safe_load(task_yaml.read_text())
    except Exception:
        return []
    expected = (task or {}).get("expected_outputs", [])
    return [str(e) for e in expected] if isinstance(expected, list) else []


def extract_stream_json_stats(transcript_path: Path) -> dict | None:
    """Parse a stream-json transcript and return stats from the final result
    message: total_cost_usd, input_tokens, output_tokens, num_turns,
    duration_ms. Returns None if no result message is found."""
    if not transcript_path.exists():
        return None
    stats = None
    try:
        with transcript_path.open(errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "result" or "total_cost_usd" in obj:
                    usage = obj.get("usage", {}) or {}
                    stats = {
                        "total_cost_usd": obj.get("total_cost_usd"),
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "num_turns": obj.get("num_turns"),
                        "duration_ms": obj.get("duration_ms"),
                    }
    except OSError:
        return None
    return stats


def scan_outputs(outputs: Path) -> tuple[list[Path], bool]:
    """Return (files, nested_detected). Skips anything under outputs/outputs/."""
    files: list[Path] = []
    nested = False
    if not outputs.is_dir():
        return files, nested
    for path in sorted(outputs.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(outputs)
        if "outputs" in rel.parts[:-1]:  # a directory named outputs inside outputs/
            nested = True
            continue
        files.append(path)
    return files, nested


def generate_report(trial_dir: Path, task_yaml: Path | None = None) -> Path:
    meta = read_meta(trial_dir)
    condition = meta.get("condition", "unknown")
    exit_code = meta.get("exit_code", "?")

    trial_id = html_mod.escape(str(meta.get("trial_id", trial_dir.name)))
    task = html_mod.escape(str(meta.get("task", "unknown")))
    run_id = html_mod.escape(str(meta.get("run", "?")))
    elapsed = html_mod.escape(str(meta.get("elapsed_seconds", "?")))
    timestamp = html_mod.escape(str(meta.get("timestamp", "")))

    condition_label = "WITH harness" if condition == "with" else "WITHOUT harness"
    badge_color = "#2e7d32" if condition == "with" else "#c62828"

    outputs = trial_dir / "outputs"
    sections = []

    # --- Trial statistics (cost/tokens/turns/duration/exit code) ---
    stats = extract_stream_json_stats(trial_dir / "transcript.txt")
    rows = []

    def fmt(v, spec=None):
        if v is None or v == "?":
            return "—"
        return format(v, spec) if spec else str(v)

    if stats:
        rows.append(("Cost (USD)", fmt(stats.get("total_cost_usd"), ",.4f")
                     if isinstance(stats.get("total_cost_usd"), (int, float)) else fmt(stats.get("total_cost_usd"))))
        rows.append(("Input tokens", fmt(stats.get("input_tokens"), ",")
                     if isinstance(stats.get("input_tokens"), int) else fmt(stats.get("input_tokens"))))
        rows.append(("Output tokens", fmt(stats.get("output_tokens"), ",")
                     if isinstance(stats.get("output_tokens"), int) else fmt(stats.get("output_tokens"))))
        rows.append(("Turns", fmt(stats.get("num_turns"))))
        if stats.get("duration_ms") is not None:
            rows.append(("API duration", f"{stats['duration_ms'] / 1000:,.1f}s"))
    rows.append(("Wall-clock duration", f"{elapsed}s"))
    rows.append(("Exit code", fmt(exit_code)))
    stats_html = "".join(
        f"<tr><th>{html_mod.escape(k)}</th><td>{html_mod.escape(str(v))}</td></tr>" for k, v in rows
    )
    note = "" if stats else '<p class="missing">No stream-json result message found in transcript.</p>'
    sections.append(f'<h2>Trial Statistics</h2>{note}<table class="data-table stats">{stats_html}</table>')

    # --- Expected outputs (task-driven) ---
    expected = load_expected_outputs(task_yaml)
    if expected:
        frags = []
        for name in expected:
            path = outputs / name
            if path.exists():
                frags.append(render_file(path, name))
            else:
                frags.append(f"<h3><code>{html_mod.escape(name)}</code></h3>"
                             '<p class="missing">expected output not found</p>')
        sections.append("<h2>Expected Outputs</h2>\n" + "\n".join(frags))

    # --- All outputs gallery ---
    files, nested = scan_outputs(outputs)
    gallery = ["<h2>All Outputs</h2>"]
    if nested:
        gallery.append('<p class="warning">Note: nested <code>outputs/outputs/</code> '
                       "directory detected — nested copies were skipped to avoid double-counting.</p>")
    if not files:
        gallery.append('<p class="missing">No files found in outputs/.</p>')
    else:
        for path in files:
            gallery.append(render_file(path, str(path.relative_to(outputs))))
    sections.append("\n".join(gallery))

    body = "\n\n".join(sections)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Trial Report — {trial_id}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 1100px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.5rem; }}
  .badge {{ display: inline-block; padding: 4px 14px; border-radius: 4px;
            color: #fff; font-weight: 600; font-size: 1.1rem;
            background: {badge_color}; }}
  .meta {{ color: #555; margin-bottom: 2rem; }}
  .meta span {{ margin-right: 2rem; }}
  h2 {{ margin-top: 2.5rem; border-bottom: 1px solid #ccc; padding-bottom: 0.3rem; }}
  h3 {{ margin-top: 1.5rem; }}
  .data-table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; margin: 1rem 0; }}
  .data-table th {{ background: #f5f5f5; text-align: left; padding: 6px 10px;
                    border-bottom: 2px solid #ccc; }}
  .data-table td {{ padding: 5px 10px; border-bottom: 1px solid #eee; }}
  .data-table tr:hover td {{ background: #fafafa; }}
  .data-table.stats {{ width: auto; min-width: 320px; }}
  figure {{ margin: 1rem 0; }}
  figure img {{ max-width: 100%; border: 1px solid #ddd; }}
  figcaption {{ color: #666; font-size: 0.8rem; margin-top: 0.3rem; }}
  .missing {{ color: #999; font-style: italic; }}
  .warning {{ color: #b26a00; background: #fff8e1; padding: 0.5rem 0.8rem;
              border-radius: 4px; }}
  pre.memo {{ background: #f8f8f8; padding: 1rem; border-radius: 4px;
              white-space: pre-wrap; word-wrap: break-word; font-size: 0.85rem;
              max-height: 600px; overflow-y: auto; }}
</style>
</head>
<body>
<h1>Trial Report <span class="badge">{condition_label}</span></h1>
<div class="meta">
  <span><strong>Trial:</strong> {trial_id}</span>
  <span><strong>Task:</strong> {task}</span>
  <span><strong>Run:</strong> {run_id}</span>
  <span><strong>Duration:</strong> {elapsed}s</span>
  <span><strong>Timestamp:</strong> {timestamp}</span>
</div>

{body}

</body>
</html>
"""

    report_path = trial_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path


def main():
    parser = argparse.ArgumentParser(description="Generate HTML report for an eval trial.")
    parser.add_argument("--trial-dir", required=True, help="Path to trial results directory.")
    parser.add_argument("--task", help="Optional path to the task YAML (for expected_outputs).")
    args = parser.parse_args()

    trial_dir = Path(args.trial_dir)
    if not trial_dir.exists():
        print(f"Trial directory not found: {trial_dir}")
        return

    task_yaml = Path(args.task) if args.task else None
    report_path = generate_report(trial_dir, task_yaml=task_yaml)
    print(f"Report saved to {report_path}")


if __name__ == "__main__":
    main()
