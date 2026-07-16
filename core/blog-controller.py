import subprocess
import sys
import os
import datetime
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REDDIT_SCRIPT = os.path.join(ROOT_DIR, "core", "reddit_pull.py")
GENERATE_SCRIPT = os.path.join(ROOT_DIR, "core", "blog_linkedin_maker.py")
PUSH_SCRIPT = os.path.join(ROOT_DIR, "core", "github-pusher.py")


LAST_RUN_MARKER = Path.home() / ".resuka_pipeline_last_run"

def already_ran_today():
    if not LAST_RUN_MARKER.exists():
        return False
    last_run = LAST_RUN_MARKER.read_text().strip()
    return last_run == datetime.date.today().isoformat()

def mark_ran_today():
    LAST_RUN_MARKER.write_text(datetime.date.today().isoformat())


def run_step(script_path, label):
    print(f"\n=== Running {label} ({script_path}) ===\n")
    result = subprocess.run(
        [sys.executable, script_path],
        cwd=ROOT_DIR,
    )
    if result.returncode != 0:
        print(f"\n{label} exited with code {result.returncode}. Stopping pipeline.")
        return False
    print(f"\n=== {label} finished successfully ===\n")
    return True


def main():
    if not run_step(REDDIT_SCRIPT, "Reddit fetch + prompt generation"):
        sys.exit(1)

    if not run_step(GENERATE_SCRIPT, "Blog + LinkedIn content generation"):
        sys.exit(1)

    if not run_step(PUSH_SCRIPT, "Push generated content to GitHub"):
        sys.exit(1)

    print("\nPipeline complete. Blog posts and LinkedIn posts have been generated and pushed.")


if __name__ == "__main__":
    if already_ran_today():
        print("Pipeline already ran today, skipping.")
    else:
        main()
        mark_ran_today()
