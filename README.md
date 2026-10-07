# claude-mem + Obsidian Second Brain

This repo sets up two things on your computer:

1. **[claude-mem](https://github.com/thedotmack/claude-mem)** is an open-source Claude Code plugin by thedotmack, licensed Apache-2.0. It records what happens in each Claude Code session and loads the relevant context back into future sessions.
2. **An Obsidian "second brain" vault** organized with PARA folders. claude-mem's memories are copied into the vault automatically as linked, tagged Markdown notes.

```
Claude Code session ──► claude-mem (~/.claude-mem/claude-mem.db)
                                   │  SessionStart / Stop hook
                                   ▼
                     claude_mem_to_obsidian.py
                                   │
                                   ▼
            Obsidian vault / Claude Memory / Projects, Sessions
```

> Run all of this on **your own computer** (macOS, Linux, or WSL on Windows), where Claude Code and Obsidian are installed.

## Requirements

- [Claude Code](https://code.claude.com)
- [Node.js](https://nodejs.org) 18 or newer (the claude-mem installer runs through `npx`)
- Python 3.9 or newer (the sync script uses only the standard library)
- [Obsidian](https://obsidian.md)

## Quick start

```bash
git clone https://github.com/Amyers1234/claude-mem.git
cd claude-mem
./setup.sh                                  # vault at ~/Documents/SecondBrain
# or
./setup.sh --vault ~/Obsidian/SecondBrain   # pick your own location / existing vault
```

`setup.sh` does the following:

1. Runs `npx claude-mem install`, the official claude-mem installer. The installer is interactive: it offers a sign-in for claude-mem's hosted observer, which comes with a free trial. You can skip that and pick your own provider, including your Anthropic plan. See [claude-mem's docs](https://docs.claude-mem.ai/installation).
2. Copies the vault template into your vault folder. It **never overwrites** files that already exist, so pointing it at an existing vault is safe.
3. Installs the sync script to `~/.claude-mem-obsidian/`.
4. Adds `SessionStart` and `Stop` hooks to `~/.claude/settings.json`, so the vault updates on its own. A backup is saved as `settings.json.bak`, your existing hooks are kept, and re-running the script never adds a duplicate hook.

Then:

1. In Obsidian, choose **Open folder as vault** and select your vault folder.
2. Restart Claude Code and work as usual. claude-mem's live viewer is at <http://localhost:37777>.
3. Your sessions show up in **Claude Memory/** in the vault.

Options: `--skip-claude-mem` if claude-mem is already installed; `--no-hook` if you'd rather sync by hand.

### Installing claude-mem by hand

If you'd rather not use the script, run this inside Claude Code:

```
/plugin marketplace add thedotmack/claude-mem
/plugin install claude-mem
```

Or run `npx claude-mem install` in a terminal. Note that `npm install -g claude-mem` installs only the library: it does not register the hooks.

## What lands in the vault

```
SecondBrain/
├── Home.md                 start page and weekly review checklist
├── CLAUDE.md               rules for Claude when you run Claude Code inside the vault
├── 00 Inbox/               quick captures (new notes go here by default)
├── 10 Projects/            goal-oriented work
├── 20 Areas/               ongoing responsibilities
├── 30 Resources/           reference material
├── 40 Archive/             inactive items
├── Daily/                  daily notes (core plugin pre-configured)
├── Templates/              Daily, Project, Note
└── Claude Memory/          ← generated from claude-mem; don't edit
    ├── Claude Memory.md    index: every project, session count, last active date
    ├── Projects/<project>.md
    └── Sessions/<date> <project> <id>.md
```

Each **session note** contains:

- Frontmatter with the date, project, and observation types.
- Tags `#project/<name>` and `#concept/<concept>`, so the tag pane and graph view show what you've worked on.
- The claude-mem session summary: request, what was investigated, learned and completed, next steps, and files edited.
- Every observation Claude recorded (🐛 bugfix, ✨ feature, ⚖️ decision, 🔍 discovery, ♻️ refactor, 📝 change), with its facts and the files it touched.

Each **project note** lists that project's sessions, its most common concepts, and the latest next steps.

To connect your own notes to this, link from a project note: `[[Claude Memory/Projects/my-app]]`. The **Project** template does this for you when the note's name matches the claude-mem project name, which is the name of the folder you ran Claude Code in.

## Syncing by hand

```bash
python3 ~/.claude-mem-obsidian/claude_mem_to_obsidian.py --vault ~/Documents/SecondBrain
python3 scripts/claude_mem_to_obsidian.py --vault ~/Documents/SecondBrain --project my-app
```

| Option | Default | Description |
| --- | --- | --- |
| `--vault` | `$OBSIDIAN_VAULT` | Path to the vault |
| `--db` | `~/.claude-mem/claude-mem.db` | claude-mem database (respects `CLAUDE_MEM_DATA_DIR`) |
| `--folder` | `Claude Memory` | Vault folder to write into |
| `--project` | all | Only export one project |
| `--quiet` | off | No output, and exits cleanly when there's no data yet (used by the hooks) |

How the sync behaves:

- It opens the claude-mem database **read-only**.
- It writes only inside the `Claude Memory/` folder.
- It rewrites a file only when its content changed, which keeps Obsidian Sync and git diffs quiet.
- claude-mem writes session summaries in the background, so a summary can show up one sync late. The `SessionStart` hook picks it up the next time you open Claude Code.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests build a fake claude-mem database that uses the real table layout, including an older layout with fewer columns, and check the generated notes.

## Uninstall

- **claude-mem:** `npx claude-mem uninstall`. Your data in `~/.claude-mem` is kept.
- **Sync:** remove the two `claude_mem_to_obsidian.py` hooks from `~/.claude/settings.json`, then delete `~/.claude-mem-obsidian/`.
- **Vault:** it's just a folder. Keep it or delete it.
