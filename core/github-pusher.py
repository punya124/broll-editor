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

GITHUB_BLOG_DIR = "content/blog"
GITHUB_LINKEDIN_DIR = "content/linkedin"

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


def push_file_to_github(local_path, repo_path, token, commit_message):
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
            f"GitHub push failed for {repo_path}: {resp.status_code} {resp.text}"
        )

    return resp.json()


def push_files_by_extension(content_dir, extension, github_target_dir, token, label):
    files = sorted(f for f in os.listdir(content_dir) if f.endswith(extension))
    if not files:
        print(f"No {extension} files found. Nothing to push to {github_target_dir}.")
        return 0, 0

    pushed = 0
    failed = 0

    for filename in files:
        local_path = content_dir / filename
        if not local_path.is_file():
            continue

        repo_path = f"{github_target_dir}/{filename}"
        commit_message = f"Added {filename} to {label}"

        print(f"Pushing {filename} -> {repo_path} ...")
        try:
            push_file_to_github(local_path, repo_path, token, commit_message)
            print(f"  Pushed. Commit: \"{commit_message}\"")
            pushed += 1

            os.remove(local_path)
            print(f"  Deleted local copy: {local_path}")

        except Exception as exc:
            print(f"  Failed to push {filename}: {exc}")
            failed += 1

    return pushed, failed


def push_generated_content():
    token = get_github_token()
    content_dir = config.OUTPUTS_DIR / "generated_content"

    if not content_dir.exists():
        print(f"No generated_content folder found at {content_dir}. Nothing to push.")
        return

    blog_pushed, blog_failed = push_files_by_extension(
        content_dir, ".mdx", GITHUB_BLOG_DIR, token, "blog posts"
    )
    linkedin_pushed, linkedin_failed = push_files_by_extension(
        content_dir, ".txt", GITHUB_LINKEDIN_DIR, token, "linkedin posts"
    )

    total_pushed = blog_pushed + linkedin_pushed
    total_failed = blog_failed + linkedin_failed

    print(f"\nDone. Pushed {total_pushed} file(s) total "
          f"({blog_pushed} blog, {linkedin_pushed} linkedin), {total_failed} failure(s).")

    if total_failed:
        sys.exit(1)


if __name__ == "__main__":
    push_generated_content()