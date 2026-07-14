# Broll Editor

Broll Editor is a small local content studio with two related workflows:

1. Blog Builder: turns recent Reddit conversation into blog post and LinkedIn content.
2. Reel Maker: creates vertical video edits from a personal library of B-roll footage.

This repository currently contains both directions of the project, but the blog-builder workflow is the part that is wired up through the controller script in [core/blog-controller.py](core/blog-controller.py).

## 1. Blog Builder

The blog-builder workflow is a three-step pipeline that runs from the controller script:

- Step 1: fetch recent Reddit posts and save prompt ideas
- Step 2: generate MDX blog posts and LinkedIn posts with Gemini
- Step 3: push generated content to GitHub

### What it does

The pipeline is orchestrated by [core/blog-controller.py](core/blog-controller.py) and calls these scripts in order:

- [core/reddit_pull.py](core/reddit_pull.py): pulls recent posts from a curated set of Reddit subreddits, filters out duplicates, and writes prompt suggestions to [data/outputs/reddit_blog_prompt_suggestions.json](data/outputs/reddit_blog_prompt_suggestions.json)
- [core/blog_linkedin_maker.py](core/blog_linkedin_maker.py): reads those suggestions, uses Gemini to generate blog content in MDX format plus a LinkedIn post, and saves the output in [data/outputs/generated_content](data/outputs/generated_content)
- [core/github-pusher.py](core/github-pusher.py): uploads the generated MDX files to a GitHub repository under the content/blog path

### What you need

Before running the blog builder, make sure you have:

- Python installed with the packages from [requirements.txt](requirements.txt)
- A Gemini API key available as GEMINI_API_KEY
- A GitHub token available as GITHUB_TOKEN if you want the output pushed to GitHub
- Network access so the scripts can reach Reddit and the Gemini API

### Setup

1. Install the Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set the required environment variables. A simple option is to export them in your shell:
   ```bash
   export GEMINI_API_KEY="your-gemini-key"
   export GITHUB_TOKEN="your-github-token"
   ```

   You can also place these values in a .env file at the project root if you prefer, since the project loads environment variables from there.

3. Run the blog-builder controller:
   ```bash
   python core/blog-controller.py
   ```

### What happens when you run it

Running the controller will execute the pipeline in order:

1. Reddit fetch and prompt generation
2. Blog and LinkedIn content creation
3. GitHub upload for generated MDX files

The script will create or update these outputs:

- [data/outputs/reddit_blog_prompt_suggestions.json](data/outputs/reddit_blog_prompt_suggestions.json)
- [data/outputs/generated_content](data/outputs/generated_content)
- [data/existing_paths.json](data/existing_paths.json)

### Notes

- The blog builder depends on Gemini being available, so it will stop early if no API key is found.
- If the suggestions file is missing, the blog-generation step will not have anything to work from.
- The GitHub push step is optional in the sense that the content is still generated locally, but it will fail unless GITHUB_TOKEN is configured.

## 2. Reel Maker

The reel-maker side of the project is the separate video-generation workflow for creating short vertical videos from a personal collection of footage. It is not part of the blog-controller pipeline.

### What it needs

- FFmpeg installed and available on your PATH
- A Gemini API key
- A folder of source video clips to analyze and assemble
- The Python dependencies from [requirements.txt](requirements.txt)

### How to run it

Start the local app:

```bash
python app.py
```

Then open the app in your browser at:

```text
http://localhost:8080
```

This section is intentionally brief for now, since the current README focus is the blog-builder workflow that runs through [core/blog-controller.py](core/blog-controller.py).
