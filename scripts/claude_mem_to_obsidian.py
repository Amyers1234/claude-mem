#!/usr/bin/env python3
"""Export claude-mem memories into an Obsidian vault.

Reads the claude-mem SQLite database (read-only) and writes Markdown notes into
a dedicated folder of the vault (default: "Claude Memory"):

    Claude Memory/
      Claude Memory.md            index of all projects
      Projects/<project>.md       one note per project, linking its sessions
      Sessions/<date> <project> <id>.md
                                  one note per session: summary + observations

Only files inside that folder are ever written, so the rest of the vault is
untouched. Files are rewritten only when their content changes, so running the
script repeatedly (e.g. from a hook or cron) is cheap and sync-friendly.

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("CLAUDE_MEM_DATA_DIR", Path.home() / ".claude-mem")) / "claude-mem.db"
DEFAULT_FOLDER = "Claude Memory"

TYPE_ICONS = {
    "decision": "⚖️",
    "bugfix": "🐛",
    "feature": "✨",
    "refactor": "♻️",
    "discovery": "🔍",
    "change": "📝",
}


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def parse_list(value) -> list[str]:
    """claude-mem stores lists as JSON strings; tolerate null/plain text."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    text = str(value).strip()
    if not text or text == "null":
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    if isinstance(parsed, list):
        return [str(v) for v in parsed if v]
    if parsed in (None, ""):
        return []
    return [str(parsed)]


def safe_name(text: str, limit: int = 80) -> str:
    """Make a string usable as an Obsidian file name / wikilink target."""
    text = re.sub(r'[\\/:*?"<>|#^\[\]]', "-", str(text))
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return (text or "untitled")[:limit]


def tag(text: str) -> str:
    """Turn a concept or project name into an Obsidian tag fragment."""
    text = re.sub(r"[^\w/-]+", "-", str(text).lower()).strip("-")
    return text or "untagged"


def date_of(epoch_ms, iso) -> str:
    if epoch_ms:
        # claude-mem stores epoch milliseconds; accept seconds too.
        seconds = epoch_ms / 1000 if epoch_ms > 10**11 else epoch_ms
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")
    return str(iso or "")[:10] or "unknown-date"


def yaml_str(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def load(conn: sqlite3.Connection, project_filter: str | None):
    conn.row_factory = sqlite3.Row
    obs_cols = columns(conn, "observations")
    sum_cols = columns(conn, "session_summaries")
    ses_cols = columns(conn, "sdk_sessions")

    def proj_expr(cols: set[str], alias: str) -> str:
        if "merged_into_project" in cols:
            return f"COALESCE({alias}.merged_into_project, {alias}.project)"
        return f"{alias}.project"

    want_obs = [c for c in (
        "id", "memory_session_id", "type", "title", "subtitle", "text", "narrative",
        "facts", "concepts", "files_read", "files_modified", "prompt_number",
        "created_at", "created_at_epoch",
    ) if c in obs_cols]
    obs_sql = (
        f"SELECT {', '.join('o.' + c for c in want_obs)}, {proj_expr(obs_cols, 'o')} AS project "
        "FROM observations o ORDER BY o.created_at_epoch, o.id"
    )
    observations = [dict(r) for r in conn.execute(obs_sql)]

    summaries = {}
    if sum_cols:
        want_sum = [c for c in (
            "memory_session_id", "request", "investigated", "learned", "completed",
            "next_steps", "files_read", "files_edited", "notes", "created_at", "created_at_epoch",
        ) if c in sum_cols]
        sum_sql = (
            f"SELECT {', '.join('s.' + c for c in want_sum)}, {proj_expr(sum_cols, 's')} AS project "
            "FROM session_summaries s ORDER BY s.created_at_epoch, s.id"
        )
        for r in conn.execute(sum_sql):
            # A session can have several summaries in newer versions; keep the latest.
            summaries[r["memory_session_id"]] = dict(r)

    sessions = {}
    if ses_cols:
        want_ses = [c for c in ("memory_session_id", "project", "user_prompt", "started_at", "started_at_epoch")
                    if c in ses_cols]
        for r in conn.execute(f"SELECT {', '.join(want_ses)} FROM sdk_sessions WHERE memory_session_id IS NOT NULL"):
            sessions[r["memory_session_id"]] = dict(r)

    obs_by_session: dict[str, list[dict]] = defaultdict(list)
    for o in observations:
        obs_by_session[o["memory_session_id"]].append(o)

    grouped: dict[str, dict] = {}
    for sid in set(summaries) | set(obs_by_session):
        meta = sessions.get(sid, {})
        summary = summaries.get(sid)
        obs = obs_by_session.get(sid, [])
        project = (summary or {}).get("project") or (obs[0]["project"] if obs else None) or meta.get("project") or "unknown"
        if project_filter and project != project_filter:
            continue
        epoch = meta.get("started_at_epoch") or (obs[0].get("created_at_epoch") if obs else None) \
            or (summary or {}).get("created_at_epoch")
        iso = meta.get("started_at") or (obs[0].get("created_at") if obs else None) or (summary or {}).get("created_at")
        grouped[sid] = {
            "id": sid,
            "project": project,
            "date": date_of(epoch, iso),
            "epoch": epoch or 0,
            "prompt": meta.get("user_prompt"),
            "summary": summary,
            "observations": obs,
        }
    return grouped


def session_note_name(s: dict) -> str:
    return safe_name(f"{s['date']} {s['project']} {str(s['id'])[:8]}")


def render_session(s: dict, folder: str) -> str:
    summary = s["summary"] or {}
    concepts = sorted({tag(c) for o in s["observations"] for c in parse_list(o.get("concepts"))})
    types = sorted({o.get("type") for o in s["observations"] if o.get("type")})
    tags = ["claude-mem", f"project/{tag(s['project'])}"] + [f"concept/{c}" for c in concepts]

    title = (summary.get("request") or s.get("prompt") or "").strip().splitlines()
    title = title[0][:120] if title else f"Session {str(s['id'])[:8]}"

    lines = [
        "---",
        f"title: {yaml_str(title)}",
        f"date: {s['date']}",
        f"project: {yaml_str(s['project'])}",
        f"session: {yaml_str(s['id'])}",
        f"observation_types: [{', '.join(types)}]",
        "tags:",
        *[f"  - {t}" for t in tags],
        "source: claude-mem",
        "---",
        "",
        f"# {title}",
        "",
        f"Project: [[{folder}/Projects/{safe_name(s['project'])}|{s['project']}]] · {s['date']}",
        "",
    ]

    sections = [
        ("Request", "request"),
        ("Investigated", "investigated"),
        ("Learned", "learned"),
        ("Completed", "completed"),
        ("Next steps", "next_steps"),
        ("Notes", "notes"),
    ]
    if summary:
        lines.append("## Summary")
        lines.append("")
        for heading, key in sections:
            value = (summary.get(key) or "").strip()
            if value:
                lines += [f"### {heading}", "", value, ""]
        edited = parse_list(summary.get("files_edited"))
        if edited:
            lines += ["### Files edited", "", *[f"- `{f}`" for f in edited], ""]

    if s["observations"]:
        lines += ["## Observations", ""]
        for o in s["observations"]:
            icon = TYPE_ICONS.get(o.get("type"), "•")
            heading = (o.get("title") or o.get("text") or "Observation").strip().splitlines()[0][:120]
            lines.append(f"### {icon} {heading}")
            lines.append("")
            if o.get("subtitle"):
                lines += [f"*{o['subtitle'].strip()}*", ""]
            body = (o.get("narrative") or (o.get("text") if o.get("title") else "") or "").strip()
            if body:
                lines += [body, ""]
            facts = parse_list(o.get("facts"))
            if facts:
                lines += [*[f"- {f}" for f in facts], ""]
            obs_concepts = parse_list(o.get("concepts"))
            if obs_concepts:
                lines += ["Concepts: " + " ".join(f"#concept/{tag(c)}" for c in obs_concepts), ""]
            modified = parse_list(o.get("files_modified"))
            if modified:
                lines += ["Files modified: " + ", ".join(f"`{f}`" for f in modified), ""]
    return "\n".join(lines).rstrip() + "\n"


def render_project(project: str, sessions: list[dict], folder: str) -> str:
    sessions = sorted(sessions, key=lambda s: (s["date"], s["epoch"]), reverse=True)
    concept_counts: dict[str, int] = defaultdict(int)
    for s in sessions:
        for o in s["observations"]:
            for c in parse_list(o.get("concepts")):
                concept_counts[c] += 1
    top = sorted(concept_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]

    lines = [
        "---",
        f"project: {yaml_str(project)}",
        "tags:",
        "  - claude-mem",
        f"  - project/{tag(project)}",
        "source: claude-mem",
        "---",
        "",
        f"# {project}",
        "",
        f"{len(sessions)} sessions · {sum(len(s['observations']) for s in sessions)} observations",
        "",
    ]
    if top:
        lines += ["## Top concepts", "", " ".join(f"#concept/{tag(c)}" for c, _ in top), ""]
    latest = next((s for s in sessions if s["summary"] and (s["summary"].get("next_steps") or "").strip()), None)
    if latest:
        lines += ["## Latest next steps", "", latest["summary"]["next_steps"].strip(), ""]
    lines += ["## Sessions", ""]
    for s in sessions:
        summary = s["summary"] or {}
        label = (summary.get("request") or s.get("prompt") or "").strip().splitlines()
        label = label[0][:100] if label else f"{len(s['observations'])} observations"
        lines.append(f"- [[{folder}/Sessions/{session_note_name(s)}|{s['date']}]] — {label}")
    return "\n".join(lines).rstrip() + "\n"


def render_index(by_project: dict[str, list[dict]], folder: str) -> str:
    lines = [
        "---",
        "tags:",
        "  - claude-mem",
        "source: claude-mem",
        "---",
        "",
        "# Claude Memory",
        "",
        "Generated from claude-mem by `claude_mem_to_obsidian.py`. Do not edit notes in this",
        "folder by hand — they are overwritten on the next sync. Link to them from your own notes instead.",
        "",
        "| Project | Sessions | Last active |",
        "| --- | --- | --- |",
    ]
    rows = sorted(by_project.items(), key=lambda kv: max(s["date"] for s in kv[1]), reverse=True)
    for project, sessions in rows:
        last = max(s["date"] for s in sessions)
        lines.append(f"| [[{folder}/Projects/{safe_name(project)}\\|{project}]] | {len(sessions)} | {last} |")
    return "\n".join(lines) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def export(db_path: Path, vault: Path, folder: str = DEFAULT_FOLDER, project: str | None = None) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(
            f"claude-mem database not found at {db_path}. Install claude-mem and run a Claude Code session first."
        )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        sessions = load(conn, project)
    finally:
        conn.close()

    root = vault / folder
    by_project: dict[str, list[dict]] = defaultdict(list)
    written = 0
    for s in sessions.values():
        by_project[s["project"]].append(s)
        written += write_if_changed(root / "Sessions" / f"{session_note_name(s)}.md", render_session(s, folder))
    for name, items in by_project.items():
        written += write_if_changed(root / "Projects" / f"{safe_name(name)}.md", render_project(name, items, folder))
    if by_project:
        written += write_if_changed(root / f"{folder}.md", render_index(by_project, folder))
    return {"sessions": len(sessions), "projects": len(by_project), "written": written}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", default=os.environ.get("OBSIDIAN_VAULT"),
                        help="Path to the Obsidian vault (or set OBSIDIAN_VAULT)")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"claude-mem database (default: {DEFAULT_DB})")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="Vault folder to write into")
    parser.add_argument("--project", help="Only export this claude-mem project")
    parser.add_argument("--quiet", action="store_true", help="Print nothing on success (for hooks)")
    args = parser.parse_args(argv)

    if not args.vault:
        parser.error("--vault is required (or set OBSIDIAN_VAULT)")
    try:
        stats = export(Path(args.db).expanduser(), Path(args.vault).expanduser(), args.folder, args.project)
    except FileNotFoundError as e:
        # No data yet is normal right after install; don't make hooks report an error.
        if args.quiet:
            return 0
        print(e, file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"Synced {stats['sessions']} sessions across {stats['projects']} projects "
              f"({stats['written']} files updated) -> {Path(args.vault).expanduser() / args.folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
