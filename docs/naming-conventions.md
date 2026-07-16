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
| Command | `git-worklog` — Planned (PR 3) |

Planned subcommands: `init`, `generate`, `report`, `validate`, `doctor`,
`preview`, `apply`, `migrate`, `clean`, `version`.

## Python package

| | |
| --- | --- |
| Package | `git_worklog` — Planned (PR 3; currently loose scripts under `git-worklog/scripts/`) |

Underscores, because it is a Python identifier. This is the only place the
underscore form is correct.

## Directories

| | |
| --- | --- |
| Project output | `.git-worklog/` — Planned (PR 2; currently `PROJECT_WORKLOG/`) |
| User-level state | `~/.git-worklog/` — Planned (PR 2; currently `~/.repo_worklog/`) |
| Home override env var | `GIT_WORKLOG_HOME` — Planned (PR 2; does not exist yet) |

## Environment variables

| | |
| --- | --- |
| Model overrides | `GIT_WORKLOG_{ANTHROPIC,OPENAI,GOOGLE}_MODEL` — Planned (PR 3; currently `REPO_WORKLOG_*`) |

`SCREAMING_SNAKE_CASE`, always prefixed `GIT_WORKLOG_`.

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
- paths that a Planned rename above has not reached yet.

Neither is a bug to be fixed on sight. Check this table first.
