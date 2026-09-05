# Publishing celld

Distribution: `celld`. Import: `celld`. Python CLI: `pycelld`.
The native celld executable remains a separate prerequisite and is never
overwritten by this wheel.

The first release has not been uploaded. PyPI's JSON and Simple APIs both
returned 404 for `celld` on 2026-09-05. A missing project page does not guarantee
that PyPI will accept a name; only a successful upload claims it.

## First upload

In the owning PyPI account, add a
[pending trusted publisher](https://pypi.org/manage/account/publishing/):

| Field | Value |
|---|---|
| PyPI project | `celld` |
| GitHub owner | `sambhav` |
| Repository | `celld-python` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

After the workflow is present on the repository's default branch, run
**Python distribution** on the release commit with **publish** selected. The
workflow builds a wheel and source distribution, checks metadata, installs the
wheel in an isolated environment, and tests its import, CLI, scaffold, and bundled
runtime files before uploading. Its publish job uses a short-lived OIDC identity;
no PyPI password is stored in the repository.

A pending publisher does not reserve the name. The first successful upload
creates the PyPI project and turns the pending publisher into a normal one;
see [PyPI's instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).
Do not describe the name as claimed until that upload succeeds.

For a local build:

```sh
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
```

For subsequent releases, bump the version in `pyproject.toml` and publish the
verified release commit through the same workflow. PyPI releases are immutable.
