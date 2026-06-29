# GitHub Sync

Remote repository:

```text
https://github.com/1hb6s7t/packaging-nesting-ai
```

This checkout is configured with:

```powershell
git config core.hooksPath .githooks
```

The tracked `.githooks/post-commit` hook pushes the current branch to `origin`
after every successful local commit. Set `SKIP_AUTO_PUSH=1` only when a commit
must intentionally stay local.

Normal sync workflow:

```powershell
git status
git add <changed-files>
git commit -m "Describe the change"
```

After `git commit` succeeds, the hook runs:

```text
git push origin HEAD:<current-branch>
```

Files ignored by `.gitignore`, such as local databases, logs, `storage/`,
`tmp/`, `artifacts/`, real `.env*` files, and `node_modules/`, are not uploaded.

GitHub Actions runs `.github/workflows/ci.yml` on pushes and pull requests. The
workflow checks backend tests, frontend production build, and the release
preflight report path used for handoff evidence. The preflight job allows the
frontend gate to be skipped only because `frontend-build` is a required job in
the same workflow.

CI artifacts:

- `frontend-dist-<commit>` keeps the production frontend build for 7 days.
- `release-preflight-evidence-<commit>` keeps the preflight report,
  verification report, release handoff bundle, handoff verification report,
  dependency inventory, generated evidence-pack files, and
  `ci-evidence-manifest.json` for 30 days. The CI evidence manifest records the
  GitHub run context plus SHA-256 hashes for the uploaded release evidence and
  handoff files, so the downloaded artifact can be reviewed offline.

After downloading and extracting `release-preflight-evidence-<commit>`, verify
the artifact contents from the repository root with:

```powershell
python scripts\verify_ci_evidence_manifest.py --manifest <extract-dir>\ci-evidence-manifest.json --base-dir <extract-dir> --output <extract-dir>\ci-evidence-manifest-verification.json
```

CI runs the same verifier and uploads `ci-evidence-manifest-verification.json`
beside the manifest.

Use the latest successful CI run as the GitHub-side proof that the pushed commit
still passes the project gates.
