# Quatm Core

**The AGPLv3 runtime for real quantum experiments.**

Quatm Core is the open, headless part of the Quatm experiment-control
framework. It provides the experiment runtime, physical models, core servers,
property/data transport and shared utilities. The desktop GUI, web console,
licensing layer, analysis tools, monitoring tools and vendor driver catalogue
are separate commercial components.

## What is in this repository

```text
quatm/experiment/   Experiment lifecycle, parameters, scans and timing
quatm/physics/      Atomic/ionic physics constants and models
quatm/servers/      Properties, messaging, data/image services and RPC
quatm/utils/        Shared utilities, safe evaluation and scheduling helpers
tests/              Core-only regression tests
```

## Install

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests -q
```

The core is headless and can be used without the desktop GUI. A user project
provides `configuration/`, `experiments/` and `Data/`; see
`quatm.project_setup` for the minimal project skeleton.

## Licence

This repository is licensed under the **GNU Affero General Public License
v3.0**. See [`LICENSE`](LICENSE) and [`AGPL-3.0.txt`](AGPL-3.0.txt).

Commercial or proprietary extensions are distributed separately. The Quatm
name and logo are not granted by the AGPL; see the organisation profile for
contacts and commercial licensing.

## Status

This repository is the public core split of the Quatm platform. It is under
active development; interfaces may evolve between releases. The public core
is tested independently from the optional platform.
