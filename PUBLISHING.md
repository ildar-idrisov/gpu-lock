# Publishing gpu-lock

End-to-end checklist for getting `gpu-lock-client` onto PyPI and the server image onto GHCR. **You only do the one-time setup once.** Releases after that are: bump version → tag → push.

## Part 1 — One-time setup (do this once, never again)

### 1.1 Push the repo to GitHub

```bash
cd /home/ildar/hdd/projects/i/gpu-lock
git init -b main                # if not already a repo
git add .
git commit -m "Initial public release"
git remote add origin git@github.com:ildar-idrisov/gpu-lock.git
git push -u origin main
```

If you don't have an SSH key set up: use `https://github.com/ildar-idrisov/gpu-lock.git` and a personal access token, or `gh repo create ildar-idrisov/gpu-lock --public --source=. --push`.

### 1.2 Reserve the project name on PyPI

You can't configure Trusted Publishing for a project that doesn't exist yet. Two paths:

**Option A (recommended) — register a "pending publisher" first:**

1. Sign in at [pypi.org](https://pypi.org/account/login/), enable 2FA if you haven't.
2. Go to [pypi.org/manage/account/publishing/](https://pypi.org/manage/account/publishing/) → **Add a new pending publisher**.
3. Fill in:
   - **Project name:** `gpu-lock-client`
   - **Owner:** `ildar-idrisov`
   - **Repository name:** `gpu-lock`
   - **Workflow name:** `publish.yml`
   - **Environment name:** `pypi`
4. Save. PyPI now reserves the name and trusts the workflow to create it on first publish.

**Option B** — upload a manual sdist once with an API token, then convert. Skip if Option A worked.

Repeat the same on [test.pypi.org/manage/account/publishing/](https://test.pypi.org/manage/account/publishing/) using environment name `testpypi`. TestPyPI is for dry runs (tags like `v0.2.0-rc1`).

### 1.3 Create the matching environments on GitHub

PyPI Trusted Publishing relies on the GitHub Actions environment name as part of the OIDC claim.

1. On the repo: **Settings → Environments → New environment**.
2. Create two environments named exactly:
   - `pypi`
   - `testpypi`
3. No protection rules needed for now. Optionally add yourself as a required reviewer on `pypi` if you want a manual approval before each PyPI push (recommended for stable releases).

### 1.4 GHCR (GitHub Container Registry) — nothing to set up

Pushing the Docker image uses the built-in `GITHUB_TOKEN` and the `packages: write` permission already declared in the workflow. The first push will create the package automatically.

After the first release, go to your profile → **Packages** → `gpu-lock/server` → **Package settings** and set the package visibility to **Public** if you want others to pull it without auth.

### 1.5 Verify the workflow files

Make sure these exist in your repo:

- `.github/workflows/ci.yml` — runs tests on every push/PR.
- `.github/workflows/publish.yml` — handles tagged releases.

Both already check out, build, test, and publish without further config.

## Part 2 — Releasing a new version

This is the loop you'll run every time. Should take ~5 minutes.

### 2.1 Pre-flight on `main`

```bash
git checkout main && git pull
pytest tests/ -v        # all green
```

If you have local-only changes, finish them and merge to main first.

### 2.2 Decide the version

Follow [Semantic Versioning](https://semver.org/):

- **Patch** (`0.2.0 → 0.2.1`): bug fixes only, no API change.
- **Minor** (`0.2.1 → 0.3.0`): new feature, backward-compatible.
- **Major** (`0.3.0 → 1.0.0`): breaking API change.

For pre-release dry runs use `-rc1`, `-rc2`, `-alpha1`, `-beta1` suffixes — those go to TestPyPI only.

### 2.3 Bump the version in `client/pyproject.toml`

```bash
# Replace 0.2.0 with the new version
sed -i 's/^version = "0.2.0"/version = "0.3.0"/' client/pyproject.toml
```

Or open `client/pyproject.toml` and edit the `version = "..."` line by hand. Same for `server/pyproject.toml` if you bumped the server too — keep them in sync unless you have a reason not to.

### 2.4 Update `CHANGELOG.md`

Move items from `## [Unreleased]` into a new section:

```markdown
## [0.3.0] - 2026-04-21

### Added
- ...

### Changed
- ...

### Fixed
- ...
```

Keep `## [Unreleased]` as an empty placeholder at the top. Add the link reference at the bottom:

```markdown
[0.3.0]: https://github.com/ildar-idrisov/gpu-lock/compare/v0.2.0...v0.3.0
```

The `github-release` job extracts the matching `## [version]` section from CHANGELOG and uses it as the GitHub Release body — so write it like a public-facing release note.

### 2.5 Commit, tag, push

```bash
git add client/pyproject.toml server/pyproject.toml CHANGELOG.md
git commit -m "Release v0.3.0"
git tag -a v0.3.0 -m "v0.3.0"
git push origin main
git push origin v0.3.0
```

### 2.6 Watch the release run

Open the **Actions** tab on GitHub. The `Publish` workflow will:

1. **build** — verify the tag matches `client/pyproject.toml`, build sdist + wheel.
2. **publish-pypi** — upload to PyPI via OIDC. May require approval if you set up environment protection.
3. **docker** — build the server image and push to `ghcr.io/ildar-idrisov/gpu-lock/server:0.3.0` and `:latest`.
4. **github-release** — create a GitHub Release with the CHANGELOG section as body, attaching the dist files.

If any step fails, the published artifacts that already went out are not rolled back. Most common failure: tag/version mismatch — fix and re-tag.

### 2.7 Verify

```bash
pip install gpu-lock-client==0.3.0 --no-cache-dir
gpu-lock --version
```

Check:
- [pypi.org/project/gpu-lock-client/](https://pypi.org/project/gpu-lock-client/) shows the new release.
- The repo's **Releases** page has the new version with notes.
- `docker pull ghcr.io/ildar-idrisov/gpu-lock/server:0.3.0` works.

## Part 3 — Trying a release without committing to PyPI

For risky changes, do a dry run on TestPyPI first:

```bash
git tag -a v0.3.0-rc1 -m "v0.3.0-rc1"
git push origin v0.3.0-rc1
```

The workflow detects the pre-release suffix and pushes only to TestPyPI. Skip the PyPI / Docker / Release steps.

Install and test:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            gpu-lock-client==0.3.0rc1
```

Once happy, drop the suffix and tag the real `v0.3.0`.

## Part 4 — Yanking a bad release

If a published version is broken:

```bash
# Don't unpublish — yank instead so existing pins keep working.
# pypi UI: Project → Manage → Releases → Options → Yank
```

Yanking means new `pip install` commands won't pick the bad version, but anyone who already has it pinned keeps working. Then publish a patch fix (`0.3.1`).

## Part 5 — Bootstrapping the FIRST release

The very first time you run this:

1. Complete all of Part 1.
2. Optionally do a `v0.2.0-rc1` dry run on TestPyPI to make sure the OIDC handshake works.
3. Tag `v0.2.0` and push. `client/pyproject.toml` is already at `0.2.0`, so no bump needed for the first release.

That's it. After that, every release is just bump → tag → push.

## Troubleshooting

**`OIDC token not found`** — the GitHub environment name in the workflow doesn't match what you registered on PyPI. Check `environment.name` in `publish.yml`.

**`HTTPError 403: invalid-or-non-existent-authentication-information`** — the pending publisher claim doesn't match. Verify owner / repo / workflow filename / environment name match exactly between PyPI settings and the workflow.

**Tag pushed but workflow didn't run** — push tag separately (`git push origin v0.3.0`) and check the **Actions** tab. GitHub fires `push` events for tags only when you explicitly push the tag ref.

**`version mismatch` error in build** — `client/pyproject.toml` `version` field doesn't match the tag base. Bump the file, commit, delete the tag locally and remotely (`git push --delete origin v0.3.0`), re-tag, push.

**Image push fails with `denied`** — first time GHCR push for this repo requires creating the package. Workflow auto-creates it; if it still fails, verify `permissions: packages: write` is in the job and that GitHub Actions is enabled to write packages (Settings → Actions → General → Workflow permissions).
