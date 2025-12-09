# Python Browser Automation with Playwright

A browser automation template using Playwright for web scraping and automation tasks.

## Setup

1. **Install RCC** (if not already installed):
   - Download from [RCC releases](https://github.com/joshyorko/rcc/releases)

2. **Configure the automation:**
   - Modify `tasks.py` to define your browser automation logic
   - Update target URLs and selectors as needed

## Usage

Run the browser automation:
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

- Browser automation uses Playwright via `robocorp-browser`
- Dependencies are managed in `conda.yaml`
- Task definitions are in `robot.yaml`

## Documentation Resources

* [Playwright Documentation](https://playwright.dev/python/docs/intro)
* [RCC Documentation](https://github.com/joshyorko/rcc/blob/master/docs/README.md)
* [Robocorp Documentation](https://robocorp.com/docs) - RPA patterns and examples
* [RPA Framework](https://rpaframework.org/) - automation library reference