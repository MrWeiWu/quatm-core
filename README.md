# Quatm Core

**The AGPL-3.0 runtime for real quantum experiments.**

Quatm Core is the headless, open-source runtime of the Quatm experiment-control
framework. It provides the experiment lifecycle, physical models, core
services, property/data transport and shared utilities used to run a real
quantum-physics experiment from Python.

The desktop GUI, web console, licensing layer, analysis tools, monitoring
tools and vendor driver catalogue are maintained as a separate commercial
platform. Quatm Core is complete without them.

## What is in this repository

```text
quatm/experiment/   Experiment lifecycle, parameters, scans and timing
quatm/physics/      Atomic and ionic physics constants and models
quatm/servers/      Properties, messaging, data/image services and RPC
quatm/utils/        Shared utilities, safe evaluation and scheduling helpers
tests/              Core-only regression tests
docs/               Public documentation entry points
```

## Install and test

Python 3.9 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python -m unittest discover -s tests -t . -q
```

The distribution is named `quatm-core`; the Python package remains `quatm`:

```python
import quatm
from quatm.experiment import Experiment
```

A user project supplies `configuration/`, `experiments/` and `Data/`. A
compatible GUI platform can create the minimal skeleton on a new machine.

## Documentation

- [Documentation](https://quatm.com/en/docs/)
- [Getting started](https://quatm.com/en/docs/getting-started/)
- [Architecture and communication layer](https://quatm.com/en/docs/communication/)
- [Source repository](https://github.com/quatm-team/quatm-core)

## Licence

Quatm Core is licensed under the **GNU Affero General Public License v3.0**.
See [LICENSE](LICENSE) and [AGPL-3.0.txt](AGPL-3.0.txt).

The separate commercial platform is not covered by this licence. The Quatm
name and logo are trademarks of the Quatm Team; the AGPL does not grant
permission to use them to imply endorsement.

## Status

Quatm Core is under active development. Interfaces may evolve between
releases; incompatible changes are recorded in [CHANGELOG.md](CHANGELOG.md).
