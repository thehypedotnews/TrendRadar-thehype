# coding=utf-8
"""
Persistent state for the Bright Data layer, kept across runs and days.

TrendRadar stores news in one SQLite file per day, so it cannot tell that a
story was already seen yesterday, and GitHub Actions runners forget local
files after every run. This small database fixes both:

  seen          url_key -> first/last seen, number of runs, sources, published
                time, latest engagement score   (recurrence + cross-day dedup)
  title_sources story fingerprint -> sources    (same story, different URLs)
  usage         day -> requests, records        (daily request limit)

On GitHub Actions it is downloaded from / uploaded to the same S3/R2 bucket as
the news databases (key: state/brightdata.db). Locally it lives in
output/state/brightdata.db.
"""

import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from trendradar.core.ranking import ItemSignals

REMOTE_KEY = "state/brightdata.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    url_key TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    title_key TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    runs_seen INTEGER NOT NULL DEFAULT 1,
    sources TEXT NOT NULL DEFAULT '',
    published_at REAL,
    engagement_pct REAL NOT NULL DEFAULT 0,
    last_run REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_seen_last ON seen(last_seen);
CREATE INDEX IF NOT EXISTS idx_seen_title ON seen(title_key);
CREATE TABLE IF NOT EXISTS title_sources (
    title_key TEXT NOT NULL,
    source TEXT NOT NULL,
    last_seen REAL NOT NULL,
    PRIMARY KEY (title_key, source)
);
CREATE TABLE IF NOT EXISTS pending_jobs (
    snapshot_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    day TEXT PRIMARY KEY,
    requests INTEGER NOT NULL DEFAULT 0,
    records INTEGER NOT NULL DEFAULT 0,
    runs INTEGER NOT NULL DEFAULT 0
);
"""


class BrightDataState:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------- usage

    def usage(self, day: str) -> Tuple[int, int]:
        row = self.conn.execute("SELECT requests, records FROM usage WHERE day=?", (day,)).fetchone()
        return (row[0], row[1]) if row else (0, 0)

    def add_usage(self, day: str, requests: int, records: int) -> None:
        self.conn.execute(
            "INSERT INTO usage(day, requests, records, runs) VALUES(?,?,?,1) "
            "ON CONFLICT(day) DO UPDATE SET requests=requests+excluded.requests, "
            "records=records+excluded.records, runs=runs+1",
            (day, requests, records),
        )
        self.conn.commit()

    # ------------------------------------------------------ pending jobs
    # Scraper jobs that did not finish within one run keep running (and are billed)
    # at Bright Data; their results are picked up by the next run.

    def pending_jobs(self, max_age_hours: float = 24) -> List[Tuple[str, str, str, float]]:
        cutoff = time.time() - max_age_hours * 3600
        self.conn.execute("DELETE FROM pending_jobs WHERE started_at < ?", (cutoff,))
        self.conn.commit()
        return list(self.conn.execute(
            "SELECT snapshot_id, source, description, started_at FROM pending_jobs ORDER BY started_at"
        ))

    def add_pending_job(self, snapshot_id: str, source: str, description: str, started_at: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pending_jobs(snapshot_id, source, description, started_at) VALUES(?,?,?,?)",
            (snapshot_id, source, description, started_at),
        )
        self.conn.commit()

    def remove_pending_job(self, snapshot_id: str) -> None:
        self.conn.execute("DELETE FROM pending_jobs WHERE snapshot_id=?", (snapshot_id,))
        self.conn.commit()

    # -------------------------------------------------------------- seen

    def first_seen(self, keys: Iterable[str]) -> Dict[str, float]:
        keys = list(keys)
        out: Dict[str, float] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = "SELECT url_key, first_seen FROM seen WHERE url_key IN (%s)" % ",".join("?" * len(chunk))
            out.update({k: v for k, v in self.conn.execute(q, chunk)})
        return out

    def observe(self, rows: List[Dict], run_ts: float) -> None:
        """Record one run's items.

        rows: dicts with url_key, url, title, title_key, sources (list), published_at (ts or None),
              engagement_pct (0..1).
        """
        cur = self.conn.cursor()
        for r in rows:
            existing = cur.execute(
                "SELECT sources, runs_seen, last_run, published_at FROM seen WHERE url_key=?", (r["url_key"],)
            ).fetchone()
            new_sources = set(r["sources"])
            if existing:
                sources = set(filter(None, existing[0].split(","))) | new_sources
                runs = existing[1] + (0 if existing[2] == run_ts else 1)
                published = existing[3] if existing[3] is not None else r.get("published_at")
                cur.execute(
                    "UPDATE seen SET url=?, title=?, title_key=?, last_seen=?, runs_seen=?, sources=?, "
                    "published_at=?, engagement_pct=?, last_run=? WHERE url_key=?",
                    (r["url"], r["title"], r["title_key"], run_ts, runs, ",".join(sorted(sources)),
                     published, r["engagement_pct"], run_ts, r["url_key"]),
                )
            else:
                cur.execute(
                    "INSERT INTO seen(url_key, url, title, title_key, first_seen, last_seen, runs_seen, sources, "
                    "published_at, engagement_pct, last_run) VALUES(?,?,?,?,?,?,1,?,?,?,?)",
                    (r["url_key"], r["url"], r["title"], r["title_key"], run_ts, run_ts,
                     ",".join(sorted(new_sources)), r.get("published_at"), r["engagement_pct"], run_ts),
                )
            if r["title_key"]:
                for s in new_sources:
                    cur.execute(
                        "INSERT INTO title_sources(title_key, source, last_seen) VALUES(?,?,?) "
                        "ON CONFLICT(title_key, source) DO UPDATE SET last_seen=excluded.last_seen",
                        (r["title_key"], s, run_ts),
                    )
        self.conn.commit()

    def signals_since(self, since_ts: float) -> Dict[str, ItemSignals]:
        """Ranking inputs for everything seen after since_ts, keyed by url_key."""
        title_src: Dict[str, set] = {}
        for tk, src in self.conn.execute(
            "SELECT title_key, source FROM title_sources WHERE last_seen >= ?", (since_ts,)
        ):
            title_src.setdefault(tk, set()).add(src)
        out: Dict[str, ItemSignals] = {}
        for key, title_k, first, runs, sources, published, eng in self.conn.execute(
            "SELECT url_key, title_key, first_seen, runs_seen, sources, published_at, engagement_pct "
            "FROM seen WHERE last_seen >= ?",
            (since_ts,),
        ):
            srcs = set(filter(None, sources.split(","))) | title_src.get(title_k, set())
            out[key] = ItemSignals(
                published_ts=published,
                first_seen_ts=first,
                engagement_pct=eng,
                runs_seen=runs,
                n_sources=max(1, len(srcs)),
            )
        return out

    def prune(self, keep_days: int) -> int:
        cutoff = time.time() - keep_days * 86400
        n = self.conn.execute("DELETE FROM seen WHERE last_seen < ?", (cutoff,)).rowcount
        self.conn.execute("DELETE FROM title_sources WHERE last_seen < ?", (cutoff,))
        self.conn.execute(
            "DELETE FROM usage WHERE day < date('now', ?)", (f"-{max(keep_days, 7)} days",)
        )
        self.conn.commit()
        return n


# ----------------------------------------------------------------- syncing

def _env_remote_reader():
    """S3/R2 client from S3_* env vars, or None. Used read-only in dry runs."""
    names = ("S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL")
    if not all(os.environ.get(n) for n in names):
        return None
    try:
        from trendradar.storage.remote import RemoteStorageBackend

        return RemoteStorageBackend(
            bucket_name=os.environ["S3_BUCKET_NAME"],
            access_key_id=os.environ["S3_ACCESS_KEY_ID"],
            secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
            endpoint_url=os.environ["S3_ENDPOINT_URL"],
            region=os.environ.get("S3_REGION", ""),
        )
    except Exception as e:
        print(f"[Bright Data] could not open remote storage for reading state: {e}")
        return None


class StateSync:
    """Opens the state DB from S3/R2 (if the run uses remote storage) or from disk."""

    def __init__(self, storage_manager=None, local_dir: str = "output/state", dry_run: bool = False):
        self.storage_manager = storage_manager
        self.local_dir = local_dir
        self.dry_run = dry_run
        self._remote = None
        self._tmpdir: Optional[str] = None
        self.state: Optional[BrightDataState] = None

    def _remote_backend(self):
        backend = None
        if self.storage_manager is not None:
            try:
                backend = self.storage_manager.get_backend()
            except Exception:
                backend = None
        if getattr(backend, "s3_client", None) is not None and getattr(backend, "bucket_name", ""):
            return backend
        if self.dry_run:
            # dry run uses throw-away local news storage, but should still see the real
            # state (daily usage, already-seen items) when S3/R2 credentials are present
            return _env_remote_reader()
        return None

    def open(self) -> BrightDataState:
        self._remote = self._remote_backend()
        if self._remote is not None:
            self._tmpdir = tempfile.mkdtemp(prefix="trendradar_bd_")
            path = os.path.join(self._tmpdir, "brightdata.db")
            try:
                self._remote.s3_client.download_file(self._remote.bucket_name, REMOTE_KEY, path)
                print(f"[Bright Data] state loaded from remote storage ({REMOTE_KEY})")
            except Exception as e:
                if "404" in str(e) or "NoSuchKey" in str(e) or "Not Found" in str(e):
                    print("[Bright Data] no remote state yet, starting fresh")
                else:
                    print(f"[Bright Data] could not download state ({e}); starting fresh")
                if os.path.exists(path):
                    os.remove(path)
        else:
            path = os.path.join(self.local_dir, "brightdata.db")
            if self.dry_run and os.path.exists(path):
                # dry run must not change real state: work on a copy
                self._tmpdir = tempfile.mkdtemp(prefix="trendradar_bd_")
                tmp = os.path.join(self._tmpdir, "brightdata.db")
                shutil.copy2(path, tmp)
                path = tmp
        self.state = BrightDataState(path)
        return self.state

    def save(self) -> None:
        if self.state is None:
            return
        self.state.close()
        if self._remote is not None and not self.dry_run:
            try:
                self._remote.s3_client.upload_file(self.state.path, self._remote.bucket_name, REMOTE_KEY)
                print(f"[Bright Data] state saved to remote storage ({REMOTE_KEY})")
            except Exception as e:
                print(f"[Bright Data] WARNING: could not upload state: {e}")
        elif self.dry_run:
            print("[Bright Data] dry run: state not saved")
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self.state = None
