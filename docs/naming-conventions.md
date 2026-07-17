# Naming Conventions

The canonical names for this project. Anything that names the product, the
skill, the CLI, the package, or a data directory must match this document.

The project was originally called `repo_worklog_skill` and is being renamed to
**Git Worklog** over the v1.0 refactor. Because that rename lands across several
PRs, each name below is marked with its current state:

- **Active** — in effect now; use it.
- **Planned** — the target name; the old name is still what the code and docs
  use today. Do not "fix" these ahead of their PR, or the rename lands
  half-applied and nothing works.

## Product

| | |
| --- | --- |
| Brand name | **Git Worklog** — Active |
| Repository | `git-worklog` (`github.com/g761007/git-worklog`) — Active |

Write the brand as `Git Worklog` in prose. Never `git-worklog`, `Git-Worklog`,
or `GitWorklog` outside of an identifier that requires the slug form.

## Skill

| | |
| --- | --- |
| Skill directory | `git-worklog/` — Active |
| Skill `name` (SKILL.md frontmatter) | `git-worklog` — Active |
| Display name (`agents/openai.yaml`) | `Git Worklog` — Active |
| Invocation | `/git-worklog` — Active |

**The directory name is the invocation name.** In Claude Code, a skill installed
at `~/.claude/skills/<dir>/SKILL.md` takes its command name from `<dir>`; the
frontmatter `name` is only the display name shown in skill listings and
[defaults to the directory name][skills-docs]. Renaming one without the other
does not rename the command — it just makes the docs lie. The two must move
together, in the same commit.

(The one exception is a plugin-root `SKILL.md`, where `name` does set the command
name because there is no skill directory to take it from. That is not how this
skill ships.)

[skills-docs]: https://code.claude.com/docs/en/skills

## CLI

| | |
| --- | --- |
| Command | `git-worklog` — Active |
| Module form | `python3 -m git_worklog` — Active |

Implemented: `version`, `doctor`, `validate`. Planned: `init`, `generate`,
`report`, `preview`, `apply`, `migrate`, `clean`.

The console script exists only after `pip install`. The module form works
straight from the skill directory with nothing installed, which is how the skill
itself runs — the CLI is an option for humans and CI, never a requirement.

## Python package

| | |
| --- | --- |
| Package | `git_worklog` — Active |
| Location | `git-worklog/git_worklog/` — inside the skill directory |

Underscores, because it is a Python identifier. This is the only place the
underscore form is correct.

The package sits **inside** the skill directory rather than beside it, so that
copying `git-worklog/` into a host's skills folder yields a working skill with
nothing installed. `pyproject.toml` maps the package root there, so the same
code is also pip-installable. (Roadmap §3 draws them as siblings; that split is
PR 7's, and it must not cost the copy-to-install property.)

## Directories

| | |
| --- | --- |
| Project output | `.git-worklog/` — Active |
| Day files | `.git-worklog/days/<date>.md` — Active |
| Index | `.git-worklog/index.md` — Active |
| User-level state | `~/.git-worklog/` — Active |
| Home override env var | `GIT_WORKLOG_HOME` — Active |

The pre-v0.6 project output was a flat `PROJECT_WORKLOG/` with day files at its
root. It is still **readable** — `detect_layout()` probes for it — but not
writable; `migrate_legacy_worklog.py --from-dir` converts it.

## File markers

| | |
| --- | --- |
| Marker prefix | `GIT_WORKLOG` — Active |

Day and index files carry `<!-- GIT_WORKLOG:<date>:GENERATED:START -->`-style
markers. The pre-v0.6 `REPO_WORKLOG` prefix still **parses** (so a legacy file
can be read and migrated, and so it is still refused inside generated content
where it would corrupt a file) but is never written.

## Environment variables

| | |
| --- | --- |
| State directory | `GIT_WORKLOG_HOME` — Active (new in v0.7; there was never a `REPO_WORKLOG_HOME`) |
| Model overrides | `GIT_WORKLOG_{ANTHROPIC,OPENAI,GOOGLE}_MODEL` — Active |
| Output language | `GIT_WORKLOG_LANGUAGE` — Active (new in v0.8; there was never a `REPO_WORKLOG_LANGUAGE`) |

`SCREAMING_SNAKE_CASE`, always prefixed `GIT_WORKLOG_`.

`REPO_WORKLOG_{ANTHROPIC,OPENAI,GOOGLE}_MODEL` shipped publicly in v0.3.0–v0.4.0
and is **still honoured** — the current name wins, and a run that used the old
one reports a `DEPRECATED_ENV_VAR` warning. Removed in v2.0. Dropping it early
would silently swap a user's model back to the config default, which is the one
thing the model resolver exists to prevent.

## Versions

Distinct numbers that are easy to confuse. See issue #12 for giving the product
version a single source of truth and shipping v1.0.0.

| | |
| --- | --- |
| Product / release version | `openai.yaml` `version:`, git tags, CHANGELOG — currently `0.4.0` |
| Data-directory layout | `.git-worklog/VERSION` — currently `1` |
| Config schema | `config.json` `schema_version` — currently `1` |

The layout and schema versions describe the **on-disk data**, not the tool, and
bump only when a migration is needed. They are deliberately not tied to the
product version.

## Form summary

| Context | Form | Example |
| --- | --- | --- |
| Prose / display | Title Case, spaced | `Git Worklog` |
| Repo, skill, CLI, data dirs | kebab-case | `git-worklog` |
| Python package / module | snake_case | `git_worklog` |
| Environment variables | SCREAMING_SNAKE_CASE | `GIT_WORKLOG_HOME` |

## Historical names

`repo_worklog_skill`, `repo_worklog`, and `Repository Worklog` are retired as
product names. They remain, correctly, in:

- released CHANGELOG entries and git tags v0.1.0–v0.4.0 (history is not
  rewritten),
- paths and identifiers that a Planned rename above has not reached yet,
- the legacy names the tool must still **recognise** to migrate an old worklog:
  `PROJECT_WORKLOG/`, `docs/PROJECT_WORKLOG.md`, and the `REPO_WORKLOG` marker
  prefix. These are load-bearing — deleting them breaks migration for every
  existing user.

None of these is a bug to be fixed on sight. Check this document first.
