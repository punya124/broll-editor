import os
import requests

PEXELS_API_URL = "https://api.pexels.com/videos/search"


class PexelsClient:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get("PEXELS_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "No Pexels API key found. Set the PEXELS_API_KEY environment variable."
            )

    def search_videos(self, query, per_page=5, orientation="portrait"):
        headers = {"Authorization": self.api_key}
        params = {"query": query, "per_page": per_page, "orientation": orientation}
        resp = requests.get(PEXELS_API_URL, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for video in data.get("videos", []):
            mp4_files = sorted(
                (f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"),
                key=lambda f: (f.get("width") or 0),
                reverse=True,
            )
            if not mp4_files:
                continue
            results.append({
                "id": video["id"],
                "duration": video.get("duration"),
                "video_url": mp4_files[0]["link"],
            })
        return results

    def download_video(self, url, dest_path):
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)