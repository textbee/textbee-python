# Releasing

Releases publish to [PyPI](https://pypi.org/project/textbee/) from GitHub Actions through trusted publishing (OIDC). There is no API token secret.

## Every release

1. Bump `__version__` in `src/textbee/_version.py`.
2. Open a pull request and merge it to `main`.
3. `.github/workflows/release.yaml` sees a version with no `v<version>` tag. It runs lint, typecheck and tests, builds, publishes to PyPI, then creates the tag and the GitHub release.

A failed run leaves no tag, so pushing again retries it.

## One-time setup

### 1. PyPI pending publisher

Before the first release, add a pending trusted publisher at https://pypi.org/manage/account/publishing/:

| Field | Value |
| --- | --- |
| PyPI project name | `textbee` |
| Owner | `textbee` |
| Repository name | `textbee-python` |
| Workflow name | `release.yaml` |
| Environment name | `pypi` |

A pending publisher does not reserve the name. Only the first upload claims it, and anyone who uploads `textbee` first owns it. So add the pending publisher and the `pypi` environment before the first push to `main`. The first release then runs minutes after the code goes public.

The first successful run turns the pending publisher into a normal trusted publisher on the project.

### 2. GitHub environment and rules

PyPI trusts any run of `release.yaml` in the `pypi` environment, from any branch. These settings make sure only reviewed code on `main` can publish:

1. Settings, Environments: create `pypi`. Under deployment branches, choose "Selected branches" and allow only `main`. Required. On the GitHub Free plan this works on public repositories only. Optionally add yourself as a required reviewer.
2. Optional: Settings, Rules: add a branch ruleset on `main` that requires a pull request and passing CI.
3. Optional: Settings, Rules: add a tag ruleset on `v*` so only the release workflow creates release tags.

### 3. Moving to the textbee PyPI organization

The project starts under a personal PyPI account while the organization request is pending. After approval: Organization, Projects, "Add existing project", `textbee`. You must be an Owner of both. The trusted publisher stays attached, and `pip install textbee` does not change.

## Local checks

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest
uv build
```
