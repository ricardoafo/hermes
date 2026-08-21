# Skill Registry — hermes

Generated: 2026-08-21
Project: hermes
Source: sdd-init v3.0

---

## User Skills

| Skill | Trigger | Source |
| ------- | --------- | -------- |
| `judgment-day` | "judgment day", "judgment-day", "review adversarial", "dual review", "doble review", "juzgar", "que lo juzguen" | `~/.claude/skills/judgment-day/SKILL.md` |
| `go-testing` | Writing Go tests, using teatest, adding test coverage | `~/.claude/skills/go-testing/SKILL.md` |
| `skill-creator` | Create new skill, add agent instructions, document patterns for AI | `~/.claude/skills/skill-creator/SKILL.md` |
| `branch-pr` | Creating a pull request, opening a PR, preparing changes for review | `~/.claude/skills/branch-pr/SKILL.md` |
| `issue-creation` | Creating a GitHub issue, reporting a bug, requesting a feature | `~/.claude/skills/issue-creation/SKILL.md` |

## SDD Skills (orchestrator-managed, not user-triggered)

| Skill | Role |
| ------- | ------ |
| `sdd-init` | Initialize SDD context |
| `sdd-explore` | Investigate ideas before committing |
| `sdd-propose` | Create change proposal |
| `sdd-spec` | Write delta specifications |
| `sdd-design` | Create technical design |
| `sdd-tasks` | Break down into implementation tasks |
| `sdd-apply` | Implement tasks |
| `sdd-verify` | Validate against specs |
| `sdd-archive` | Close change and persist final state |
| `sdd-onboard` | Guided SDD walkthrough |

## Project Convention Files

None found at project root. No `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `GEMINI.md`, or `copilot-instructions.md`.

Project-level skills directory: none (no `.claude/skills/` or `.agent/skills/`).

Global conventions loaded from: `~/.claude/CLAUDE.md` (user-level, always active).

---

## Compact Rules (auto-resolved for sub-agent injection)

### judgment-day

Trigger on: "judgment day" / "dual review" / "juzgar". Launch two independent blind review sub-agents simultaneously, synthesize findings, apply fixes, re-judge. Max 2 iterations before escalating.

### go-testing

Trigger on: Go test files, teatest, Bubbletea TUI testing. Apply table-driven tests, golden file testing patterns as specified in skill file.

### skill-creator

Trigger on: user asks to create a new skill or document patterns for AI. Follow Agent Skills spec. Write frontmatter + purpose + steps + rules.

### branch-pr

Trigger on: creating PRs. Follow issue-first enforcement. PR must reference an existing issue. Use `gh pr create` with body HEREDOC.

### issue-creation

Trigger on: creating GitHub issues. Follow issue-first enforcement. Use `gh issue create`. Label and assign appropriately.
