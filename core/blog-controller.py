import subprocess
import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REDDIT_SCRIPT = os.path.join(ROOT_DIR, "core", "reddit_pull.py")  # adjust to actual filename/path
GENERATE_SCRIPT = os.path.join(ROOT_DIR, "core", "blog_linkedin_maker.py")  # adjust to actual filename/path


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

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
