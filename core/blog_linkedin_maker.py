import datetime
import json
import os
import re
import sys

import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from core import config

GEMMA_MODEL = "gemma-4-31b-it"
GEMMA_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMMA_MODEL}:generateContent"
)

BANNED_WORDS = [
    "delve", "leverage", "landscape", "crucial", "robust",
    "game-changer", "tapestry", "furthermore", "moreover",
]

BLOG_SYSTEM_INSTRUCTION = f"""You are a senior tech writer producing a blog post in Next.js MDX format.

STRICT OUTPUT RULES:
- Output raw MDX only. No markdown code fences, no explanation before or after.
- Start the file with a metadata export block exactly like this:

export const metadata = {{
  title: "...",
  description: "...",
  date: "{{DATE}}",
  categories: ["..."],
  keywords: ["...", "..."],
  image: "..." (an Unsplash URL, or omit this key entirely if none fits)
}};

- Do NOT include an H1 title anywhere in the body — the page template renders the title from metadata automatically. Start the body content directly (e.g. with an intro paragraph or an H2).
- Include a clear call-to-action encouraging the reader to try resuka.com (a resume tailoring AI app) — weave it naturally, don't just paste a link with no context.
- Never use these words or close variants: {", ".join(BANNED_WORDS)}.
- Never use em dashes. Use commas, periods, or parentheses instead.
- Vary sentence length. Write like a knowledgeable peer, not a corporate blog.
- Use H2/H3 headings for structure.
"""

LINKEDIN_SYSTEM_INSTRUCTION = f"""You write a LinkedIn post that teases a blog post without repeating it in full.

STRICT OUTPUT RULES:
- Plain text only, no markdown formatting, no code fences.
- Concise, friendly, peer-to-peer tone. Deliver one genuinely useful nugget of value.
- End with a soft nudge encouraging readers to check out the full guide (mention resuka.com naturally if relevant).
- Add 3-5 relevant hashtags on their own line at the end.
- Never use these words or close variants: {", ".join(BANNED_WORDS)}.
- Never use em dashes.
"""


def call_gemma(system_instruction, user_prompt, api_key):
    payload = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": 4096,
        },
    }
    resp = requests.post(
        f"{GEMMA_ENDPOINT}?key={api_key}",
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemma response shape: {data}") from exc


def strip_code_fences(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n```$", "", text)
    return text.strip()


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "post"


def generate_blog_and_linkedin(pattern, api_key, today_str):
    theme = pattern.get("theme", "Untitled")
    blog_prompt = pattern.get("blog_post_prompt", "")
    if not blog_prompt:
        raise ValueError(f"Pattern '{theme}' has no blog_post_prompt")

    filled_prompt = (
        f"{blog_prompt}\n\nUse today's date ({today_str}) as the date field in metadata."
    )

    blog_mdx = call_gemma(BLOG_SYSTEM_INSTRUCTION, filled_prompt, api_key)
    blog_mdx = strip_code_fences(blog_mdx)

    linkedin_prompt = (
        f"Here is the full blog post this LinkedIn post should tease:\n\n{blog_mdx}\n\n"
        "Write the LinkedIn post now, following your instructions."
    )
    linkedin_post = call_gemma(LINKEDIN_SYSTEM_INSTRUCTION, linkedin_prompt, api_key)
    linkedin_post = strip_code_fences(linkedin_post)

    return theme, blog_mdx, linkedin_post


def extract_title(mdx_text, fallback):
    match = re.search(r'title:\s*["\'](.+?)["\']', mdx_text)
    return match.group(1) if match else fallback


def main():
    api_key = getattr(config, "GEMINI_API_KEY", None) or os.environ.get("GOOGLE_AI_STUDIO_API_KEY")
    if not api_key:
        print("No API key found (expected config.GEMINI_API_KEY or GOOGLE_AI_STUDIO_API_KEY env var).")
        return

    config.ensure_dirs()
    suggestions_path = config.OUTPUTS_DIR / "reddit_blog_prompt_suggestions.json"
    if not suggestions_path.exists():
        print(f"No suggestions file found at {suggestions_path}. Run the Reddit script first.")
        return

    with open(suggestions_path) as f:
        suggestions = json.load(f)

    patterns = suggestions.get("patterns", [])
    if not patterns:
        print("No patterns found in suggestions file.")
        return

    today_str = datetime.date.today().isoformat()
    content_dir = config.OUTPUTS_DIR / "generated_content"
    os.makedirs(content_dir, exist_ok=True)

    for i, pattern in enumerate(patterns, start=1):
        theme = pattern.get("theme", f"post-{i}")
        print(f"[{i}/{len(patterns)}] Generating content for: {theme}")
        try:
            theme, blog_mdx, linkedin_post = generate_blog_and_linkedin(pattern, api_key, today_str)
        except Exception as exc:
            print(f"  Failed to generate content for '{theme}': {exc}")
            continue

        title = extract_title(blog_mdx, fallback=theme)
        slug = slugify(title)

        blog_path = content_dir / f"{today_str}-{slug}.mdx"
        linkedin_path = content_dir / f"{today_str}-{slug}-linkedin.txt"

        with open(blog_path, "w") as f:
            f.write(blog_mdx)
        with open(linkedin_path, "w") as f:
            f.write(linkedin_post)

        print(f"  Saved blog: {blog_path}")
        print(f"  Saved LinkedIn post: {linkedin_path}")

    print("Done.")


if __name__ == "__main__":
    main()
