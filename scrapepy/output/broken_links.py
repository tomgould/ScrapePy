"""Broken link tracking and reporting."""

import os
from datetime import datetime, timezone


class BrokenLinkTracker:
    def __init__(self):
        self.broken: set[str] = set()

    def add(self, url: str):
        self.broken.add(url)

    @property
    def count(self) -> int:
        return len(self.broken)

    def write_report(self, output_dir: str, source_url: str):
        """Write broken_links.txt to the output directory."""
        if not self.broken:
            return

        filepath = os.path.join(output_dir, "broken_links.txt")
        lines = [
            "Broken Links Report",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Source: {source_url}",
            f"Total broken links: {len(self.broken)}",
            "",
            "---",
            "",
            *sorted(self.broken),
        ]
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"Error log saved: {filepath}")
