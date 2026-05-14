# Python uv-native RCC Template

This template demonstrates RCC's uv-native environment mode for a plain
`robocorp.tasks` automation.

The pattern is in `conda.yaml`: there are no Conda channels. RCC installs the
pinned `python` and `uv` entries directly, then uses uv inside the RCC holotree
environment to install the packages listed under `pip:`. The host only needs
the `rcc` CLI; do not install this template's Python packages into the Bluefin
host or a separate project virtualenv for the normal smoke path.

This is not a standalone uv project. There is intentionally no
`pyproject.toml` or `uv.lock` here. If you add those later, make that a separate
project choice and update the RCC commands below.

## Run From This Directory

All commands below assume your shell is in this template root:

```bash
cd 06-python-uv-native
```

Use an isolated RCC home for local smoke checks so cache state stays out of the
host default:

```bash
export ROBOCORP_HOME=/tmp/robot-templates-06-rcc
```

Prebuild and inspect the RCC environment:

```bash
rcc ht vars -r robot.yaml
```

Run the smoke task:

```bash
rcc run -r robot.yaml -t RunTask --silent
```

The smoke command proves that RCC can resolve the uv-native environment, start
the pinned Python interpreter, import `robocorp.tasks`, and run the task entry
point.

## Expected Output

The run should exit with status code 0 and create:

```text
output/
|-- log.html
`-- uv-native-smoke.txt
```

`uv-native-smoke.txt` contains the Python version and executable path used by
the RCC-managed environment. `log.html` is the Robocorp task log captured by
RCC under the configured `artifactsDir`.

## File Boundaries

- `robot.yaml` defines the RCC task named `RunTask` and points it at the Python
  task `uv_native_smoke`.
- `conda.yaml` is the uv-native environment contract. Keep `python` and `uv` at
  the top level, and add normal Python package requirements under `pip:`.
- `tasks.py` is the automation entry point. Replace `uv_native_smoke` with your
  real task logic after the smoke run works.
- `output/` is generated at runtime and ignored by git.

## First Files To Edit

1. Edit `tasks.py` when changing the automation behavior.
2. Edit the `pip:` section in `conda.yaml` when adding Python dependencies.
3. Edit `robot.yaml` when renaming the RCC task or adding another task entry.

Keep Python, pip, uv, and RCC responsibilities separate: Python code lives in
`tasks.py`, package pins live in `conda.yaml`, uv is used by RCC inside the
managed environment, and `rcc run` is the command surface for this template.

## References

- [RCC recipes](https://github.com/joshyorko/rcc/blob/master/docs/recipes.md)
- [robocorp.tasks](https://pypi.org/project/robocorp-tasks/)
- [uv](https://github.com/astral-sh/uv)
