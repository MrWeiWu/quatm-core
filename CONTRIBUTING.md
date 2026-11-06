# Contributing to Quatm Core

Thanks for improving the public core. This repository contains only the
AGPLv3-covered runtime.

## Before opening a pull request

- Search existing issues first.
- Keep changes focused; separate refactors from behaviour changes.
- Add or update tests under `tests/`.
- Run the core test suite:

  ```bash
  python -m venv .venv
  . .venv/bin/activate
  pip install -e ".[dev]"
  python -m pytest tests -q
  ```

- Do not add private platform code, vendor SDK binaries, credentials, customer
  data or licence keys.
- Keep timing and unit semantics explicit. Changes to experiment semantics
  require a short rationale and tests.
- Do not add dependencies without an ADR/issue explaining why.

## Licensing of contributions

By submitting a contribution you confirm that you have the right to license it
under **AGPL-3.0**. Contributions are accepted under AGPL-3.0; the project may
also offer separate commercial licences for the combined product. If that
requires a contributor agreement, it will be stated in the pull request.

## Security

Do not report vulnerabilities in public issues. See [SECURITY.md](SECURITY.md).
