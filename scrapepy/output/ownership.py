"""Which URL owns each output file when several URLs map to one file.

/about and /about.html, or /news/123/slug and /news/slug-123, can map to the
same Markdown file or HTML copy. Exactly one of them keeps it, chosen by a
rule that does not depend on crawl order, concurrency or the cache: the
shortest URL wins, and among URLs of the same length the first in
alphabetical order. The owner of each file is kept in the incremental cache,
so an owner that is unchanged (and not converted again) still holds its file
in later runs.
"""


def url_rank(url: str) -> tuple[int, str]:
    """Sort key: the smallest rank wins a contested file."""
    return (len(url), url)


class FileOwners:
    """Decides, for one run, which URL writes each contested file.

    Files are named by any stable string (the caller passes paths). A page
    calls decide() with the files it would write:

    - "write": no better URL wants any of its files. Pages listed in
      `retract` held one of those files earlier in this run and now lose it.
    - "lose": a better URL already holds a file this run (`file`, `kept`).
    - "wait": the better URL is the file's owner from an earlier run and
      has not been met yet in this run. finish() settles these at the end:
      the page loses if the owner was met, otherwise it writes.
    """

    def __init__(self, persisted: dict[str, str] | None = None):
        # file -> owner from earlier runs (updated as this run decides)
        self.persisted: dict[str, str] = dict(persisted or {})
        self.initial: dict[str, str] = dict(self.persisted)
        self.run: dict[str, str] = {}          # file -> URL holding it this run
        self.met: set[str] = set()             # URLs written or unchanged this run
        self.files_of: dict[str, list[str]] = {}   # URL -> files it holds this run

    def persisted_files(self, url: str) -> list[str]:
        return sorted(f for f, o in self.persisted.items() if o == url)

    def decide(self, url: str, files: list[str], final: bool = False):
        """files are in order of preference for naming the contested file.
        With final=True (end of run), owners from earlier runs that were not
        met in this run no longer count."""
        rivals = []
        for i, f in enumerate(files):
            holder = self.run.get(f)
            if holder and holder != url:
                rivals.append((url_rank(holder), i, holder, f, "run"))
                continue
            owner = self.persisted.get(f)
            if not final and owner and owner != url and owner not in self.met:
                rivals.append((url_rank(owner), i, owner, f, "persisted"))
        better = sorted(r for r in rivals if r[0] < url_rank(url))
        if not better:
            retract = {}
            for r in sorted(rivals):
                if r[4] == "run":
                    retract.setdefault(r[2], r[3])
            return {"action": "write", "retract": sorted(retract.items())}
        _, _, best, f, kind = better[0]
        if kind == "run":
            return {"action": "lose", "file": f, "kept": best}
        return {"action": "wait", "file": f, "owner": best}

    def claim(self, url: str, files: list[str]) -> None:
        """url writes (or keeps, when unchanged) these files this run."""
        self.met.add(url)
        held = self.files_of.setdefault(url, [])
        for f in files:
            self.run[f] = url
            self.persisted[f] = url
            if f not in held:
                held.append(f)

    def release(self, url: str) -> list[str]:
        """url loses everything it held this run; returns those files."""
        files = self.files_of.pop(url, [])
        self.met.discard(url)
        for f in files:
            if self.run.get(f) == url:
                del self.run[f]
        for f, o in list(self.persisted.items()):
            if o == url:
                del self.persisted[f]
        return files

    def meet_unchanged(self, url: str):
        """An unchanged page keeps the files it owned at the start of the run,
        unless a better URL took one of them this run. Returns None, or
        (file, kept) when the page has lost its file."""
        files = sorted(f for f, o in self.initial.items() if o == url)
        for f in files:
            holder = self.run.get(f)
            if holder and holder != url and url_rank(holder) < url_rank(url):
                return f, holder
        self.claim(url, files)
        return None
