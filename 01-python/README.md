# Python Minimal Template

A minimal Python automation template for building RPA tasks with Robocorp.

## Setup

1. **Install RCC** (if not already installed):
   - Download from [RCC releases](https://github.com/joshyorko/rcc/releases)

2. **Configure your automation:**
   - Modify `tasks.py` to define your automation logic
   - Add dependencies to `conda.yaml` as needed

## Usage

Run the automation:
```bash
rcc run
```

To activate the environment for development:
```bash
rcc ht vars
# or
rcc holotree vars
```

## Results

After running the bot, check out the `log.html` under the `output` folder.

## Configuration

- Task definitions are in `robot.yaml`
- Dependencies are managed in `conda.yaml`
- Main automation logic is in `tasks.py`

## Documentation Resources

* [RCC Documentation](https://github.com/joshyorko/rcc/blob/master/docs/README.md)
* [Robocorp Documentation](https://robocorp.com/docs) - RPA patterns and examples
* [RPA Framework](https://rpaframework.org/) - automation library reference