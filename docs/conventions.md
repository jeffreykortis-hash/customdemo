# Project Conventions

Operational conventions for this workspace. For Sigma-spec naming/layout
conventions, see `.claude/skills/sigma-workbook-conventions/`.

## Folder responsibilities

| Folder | Mutable? | Purpose |
|--------|----------|---------|
| `.claude/skills/` | yes | Project-local workbook-pattern skills. Edit freely. |
| `vendor/` | no | Read-only mirror of upstream `sigma-agent-skills`. Refresh via `scripts/refresh-vendor.sh`. Gitignored. |
| `workbooks/<name>/` | yes | One folder per dashboard. Source of truth = `spec.json`. |
| `workbooks/_exemplars/` | append-only | Golden specs harvested from Sigma. Never edit in place; treat as immutable references. |
| `workbooks/_template/` | rarely | Skeleton copied for new dashboards. Keep generic. |
| `prompts/library/` | yes | Reusable prompt fragments. Markdown only. |
| `scripts/` | yes | Shell helpers. Keep thin — defer logic to skills. |
| `artifacts/` | yes | **Client discovery artifacts** — screenshots, PDFs, call transcripts fed to `sigma-discovery-brief`. Gitignored, never committed. |
| `docs/` | yes | This folder. Keep concise. |

## Secrets

- All secrets live in `.env` (gitignored). `.env.example` documents the contract.
- Source via `eval "$(scripts/load-env.sh)"`. The script never echoes values.
- Token retrieval is delegated to the upstream `sigma-api` skill.
- Never paste a token into a prompt, comment, file, or commit message.

## Client artifacts

Screenshots and call transcripts are the client's property, and a dashboard
screenshot routinely carries customer names in a detail tile.

- Keep them in `artifacts/` (gitignored). Never commit one, and never quote one in
  a commit message.
- They do not go into a spec, a `CallText` prompt (a real LLM call from *their*
  warehouse), or a workbook surface — including verbatim quotes from the call.
- `scripts/intake-artifacts.py` flags PII-shaped strings; `scripts/validate-brief.py`
  refuses to pass a brief with an unresolved flag.
- The **brief** (`brief.json`) is committable and should be, next to the generator —
  it's the record of why the build looks the way it does. Strip any quote or
  identifier you wouldn't put in a PR.

## Git hygiene

- Commit per iteration when working on a dashboard, so `git log` doubles as the
  iteration history.
- Avoid committing iteration scratch files; use `.draft.json` or `.tmp` suffixes
  (already gitignored).
- `.claude/settings.json` is committed (team default). `.claude/settings.local.json`
  is gitignored (personal overrides).
- Don't commit `vendor/`. It's gitignored — refresh on demand.

## Adding a new workbook

```bash
cp -R workbooks/_template workbooks/<dashboard-name>
```

Then describe the dashboard to Claude. The `sigma-workbook-conventions` skill
activates automatically; any domain-pattern skill you've authored (see
`skill-authoring.md`) also activates based on its `description:` frontmatter.

## Adding a new workbook-pattern skill

See `skill-authoring.md`.

## Iterating on Sigma generations

See `iteration-playbook.md`.
