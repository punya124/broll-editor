import base64
import os
import sys

import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from core import config

GITHUB_OWNER = "punya124"
GITHUB_REPO = "resume-tailor"
GITHUB_BRANCH = "main"
GITHUB_TARGET_DIR = "content/blog"  # path inside the repo

GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents"


def get_github_token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("No GITHUB_TOKEN found in environment. Create a fine-grained PAT with "
              "'Contents: Read and write' access to this repo and set it as GITHUB_TOKEN.")
        sys.exit(1)
    return token


def github_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_existing_sha(repo_path, token):
    """Returns the blob SHA if the file already exists on the branch, else None."""
    url = f"{GITHUB_API_BASE}/{repo_path}"
    resp = requests.get(url, headers=github_headers(token), params={"ref": GITHUB_BRANCH})
    if resp.status_code == 200:
        return resp.json().get("sha")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()


def push_file_to_github(local_path, repo_filename, token, commit_message):
    repo_path = f"{GITHUB_TARGET_DIR}/{repo_filename}"

    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    existing_sha = get_existing_sha(repo_path, token)

    payload = {
        "message": commit_message,
        "content": content_b64,
        "branch": GITHUB_BRANCH,
    }
    if existing_sha:
        payload["sha"] = existing_sha  # required by GitHub API when overwriting a file

    url = f"{GITHUB_API_BASE}/{repo_path}"
    resp = requests.put(url, headers=github_headers(token), json=payload)

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"GitHub push failed for {repo_filename}: {resp.status_code} {resp.text}"
        )

    return resp.json()


def push_generated_content(delete_after_push=True):
    token = get_github_token()
    content_dir = config.OUTPUTS_DIR / "generated_content"

    if not content_dir.exists():
        print(f"No generated_content folder found at {content_dir}. Nothing to push.")
        return

    files = sorted(f for f in os.listdir(content_dir) if f.endswith(".mdx"))
    if not files:
        print("No .mdx files found in generated_content. Nothing to push.")
        return

    pushed = 0
    failed = 0

    for filename in files:
        local_path = content_dir / filename
        if not local_path.is_file():
            continue

        commit_message = f"Added {filename} to blog posts"

        print(f"Pushing {filename} -> {GITHUB_TARGET_DIR}/{filename} ...")
        try:
            push_file_to_github(local_path, filename, token, commit_message)
            print(f"  Pushed. Commit: \"{commit_message}\"")
            pushed += 1

            if delete_after_push:
                os.remove(local_path)
                print(f"  Deleted local copy: {local_path}")

        except Exception as exc:
            print(f"  Failed to push {filename}: {exc}")
            failed += 1

    print(f"\nDone. Pushed {pushed} file(s), {failed} failure(s).")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    push_generated_content()
