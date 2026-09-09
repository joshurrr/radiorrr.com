# AGENTS.md

## Repository Overview

Radio RRR is a static website with no build system or package manager.

Important files:

- `index.html` - primary Radio RRR website, including HTML, CSS, and JavaScript.
- `collab.html` - creator collaboration page.
- `logo.png`, `RadioRRR.png`, `radiorrr_background.png`, `radiorrr_egypt_background.png` - site assets.
- `CNAME`, `robots.txt`, `sitemap.xml` - deployment and SEO metadata.
- `index*.html` and similarly named files - historical backups or previous page versions. Do not treat these as active pages unless explicitly requested.

## Stable Baseline

- `v1.0-stable` is the immutable reference baseline for the current production experience.
- `main` is the active production branch.
- Preserve the existing working behavior of the Radio RRR website.
- Treat the Stable v1.0 tag and commit as protected reference points.
- Do not rewrite or replace the active site wholesale without explicit approval.
- Avoid changing external stream, API, Formspree, social, or CDN integrations unless the change is specifically required and verified.

## Change Guidelines

- Inspect the existing implementation and identify the smallest safe change before modifying anything.
- Make small, incremental, focused changes.
- Prefer editing the existing structure and patterns in `index.html` or `collab.html`.
- Avoid unnecessary frameworks, dependencies, build tooling, or architectural rewrites.
- Keep unrelated historical backup files untouched.
- Preserve existing responsive behavior, audio playback, live-DJ discovery, recommendation form, navigation tabs, and external links unless the task explicitly changes them.
- Keep user-facing changes scoped and easy to review.

## Branching and Git

- Do not make significant changes directly on `main`.
- Use a feature branch for significant work, preferably with a `codex/` prefix.
- Keep commits small and focused when commits are requested.
- Do not create commits, branches, tags, or pushes unless explicitly requested.
- Never use destructive Git commands such as `git reset --hard` or `git checkout --` without explicit approval.
- Check the working tree before and after changes, and preserve unrelated user changes.

## Secrets and External Services

- Never commit API keys, passwords, access tokens, private credentials, or other secrets.
- Treat external service identifiers and endpoints as configuration-sensitive.
- Do not expose new credentials in HTML, JavaScript, documentation, or commit messages.
- Review diffs before committing to ensure secrets and unrelated changes are absent.

## Verification

Because this repository has no configured test suite or build command:

- Inspect the diff carefully after every change.
- Check that referenced local assets exist.
- Validate HTML and JavaScript syntax when practical.
- Test the affected page and interaction in a browser when practical.
- For stream/API/form changes, verify both the success path and a reasonable failure state.
- Confirm that the Stable v1.0 behavior remains intact when the change is unrelated.

## Completion Summary

When reporting completed work, summarize:

- Files changed.
- User-visible behavior changed.
- Verification performed.
- Any remaining limitations or external-service dependencies.
