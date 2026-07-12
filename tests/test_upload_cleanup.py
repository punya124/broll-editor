import tempfile
import unittest
from pathlib import Path

from core import config


class UploadCleanupTests(unittest.TestCase):
    def test_prunes_oldest_inactive_uploads_when_limit_is_exceeded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            uploads_dir = Path(tmpdir)
            for name in ("a.m4a", "b.m4a", "c.m4a", "d.m4a"):
                (uploads_dir / name).write_bytes(b"x")

            original_uploads_dir = config.UPLOADS_DIR
            config.UPLOADS_DIR = uploads_dir
            try:
                config.prune_uploads(limit=3, active_paths={uploads_dir / "c.m4a"})
            finally:
                config.UPLOADS_DIR = original_uploads_dir

            remaining = sorted(p.name for p in uploads_dir.iterdir())
            self.assertEqual(remaining, ["b.m4a", "c.m4a", "d.m4a"])


if __name__ == "__main__":
    unittest.main()
