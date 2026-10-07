import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import claude_mem_to_obsidian as sync  # noqa: E402

SCHEMA = """
CREATE TABLE sdk_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content_session_id TEXT NOT NULL,
  memory_session_id TEXT UNIQUE,
  project TEXT NOT NULL,
  user_prompt TEXT,
  started_at TEXT NOT NULL,
  started_at_epoch INTEGER NOT NULL
);
CREATE TABLE observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  text TEXT,
  type TEXT NOT NULL,
  title TEXT, subtitle TEXT, facts TEXT, narrative TEXT, concepts TEXT,
  files_read TEXT, files_modified TEXT, prompt_number INTEGER,
  created_at TEXT NOT NULL, created_at_epoch INTEGER NOT NULL
);
CREATE TABLE session_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  request TEXT, investigated TEXT, learned TEXT, completed TEXT, next_steps TEXT,
  files_read TEXT, files_edited TEXT, notes TEXT,
  created_at TEXT NOT NULL, created_at_epoch INTEGER NOT NULL
);
"""

EPOCH = 1_760_000_000_000  # 2025-10-09, milliseconds


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO sdk_sessions (content_session_id, memory_session_id, project, user_prompt, started_at, started_at_epoch)"
        " VALUES ('c1', 'mem-abc12345', 'my-app', 'Fix login bug', '2025-10-09T08:00:00Z', ?)", (EPOCH,))
    conn.execute(
        "INSERT INTO observations (memory_session_id, project, type, title, subtitle, narrative, facts, concepts,"
        " files_modified, created_at, created_at_epoch) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("mem-abc12345", "my-app", "bugfix", "Session cookie expired early", "Auth middleware",
         "The TTL was in seconds, not ms.", json.dumps(["TTL fixed to 3600s"]),
         json.dumps(["auth", "Session Cookies"]), json.dumps(["src/auth.ts"]), "2025-10-09T08:05:00Z", EPOCH + 1))
    conn.execute(
        "INSERT INTO session_summaries (memory_session_id, project, request, learned, next_steps, files_edited,"
        " created_at, created_at_epoch) VALUES (?,?,?,?,?,?,?,?)",
        ("mem-abc12345", "my-app", "Fix login bug", "Cookies use seconds", "Add a regression test",
         json.dumps(["src/auth.ts"]), "2025-10-09T08:10:00Z", EPOCH + 2))
    # Observation in a session with no summary and no sdk_sessions row; facts stored as 'null'.
    conn.execute(
        "INSERT INTO observations (memory_session_id, project, type, title, facts, concepts, created_at, created_at_epoch)"
        " VALUES ('mem-zzz99999', 'other/proj', 'discovery', 'Found config', 'null', NULL, '2025-10-10T09:00:00Z', ?)",
        (EPOCH + 86_400_000,))
    conn.commit()
    conn.close()


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "claude-mem.db"
        self.vault = self.root / "vault"
        make_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_session_project_and_index_notes(self):
        stats = sync.export(self.db, self.vault)
        self.assertEqual(stats["sessions"], 2)
        self.assertEqual(stats["projects"], 2)

        mem = self.vault / "Claude Memory"
        sessions = sorted(p.name for p in (mem / "Sessions").iterdir())
        self.assertEqual(len(sessions), 2)
        self.assertTrue(any("my-app mem-abc1" in n for n in sessions))
        # '/' in project names must not create sub-folders.
        self.assertTrue(any("other-proj" in n for n in sessions))

        note = next((mem / "Sessions").glob("*my-app*")).read_text()
        self.assertIn('title: "Fix login bug"', note)
        self.assertIn("  - concept/session-cookies", note)
        self.assertIn("### 🐛 Session cookie expired early", note)
        self.assertIn("- TTL fixed to 3600s", note)
        self.assertIn("### Next steps", note)
        self.assertIn("[[Claude Memory/Projects/my-app|my-app]]", note)

        project = (mem / "Projects" / "my-app.md").read_text()
        self.assertIn("Add a regression test", project)
        self.assertIn("#concept/auth", project)

        index = (mem / "Claude Memory.md").read_text()
        self.assertIn("my-app", index)
        self.assertIn("other/proj", index)

    def test_second_run_writes_nothing(self):
        sync.export(self.db, self.vault)
        self.assertEqual(sync.export(self.db, self.vault)["written"], 0)

    def test_project_filter(self):
        stats = sync.export(self.db, self.vault, project="my-app")
        self.assertEqual(stats["projects"], 1)

    def test_does_not_touch_other_vault_files(self):
        mine = self.vault / "00 Inbox" / "idea.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("keep me")
        sync.export(self.db, self.vault)
        self.assertEqual(mine.read_text(), "keep me")

    def test_missing_db(self):
        args = ["--vault", str(self.vault), "--db", str(self.root / "nope.db")]
        self.assertEqual(sync.main(args), 1)
        self.assertEqual(sync.main(args + ["--quiet"]), 0)

    def test_tolerates_old_schema_without_rich_columns(self):
        old = self.root / "old.db"
        conn = sqlite3.connect(old)
        conn.executescript(
            "CREATE TABLE observations (id INTEGER PRIMARY KEY, memory_session_id TEXT, project TEXT, text TEXT,"
            " type TEXT, created_at TEXT, created_at_epoch INTEGER);"
            "INSERT INTO observations VALUES (1, 's1', 'p', 'plain text obs', 'change', '2025-01-01', 1735689600000);")
        conn.close()
        stats = sync.export(old, self.vault)
        self.assertEqual(stats["sessions"], 1)
        note = next((self.vault / "Claude Memory" / "Sessions").glob("*.md")).read_text()
        self.assertIn("plain text obs", note)


if __name__ == "__main__":
    unittest.main()
