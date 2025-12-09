# Plan: Create robot-templates Repository for RCC Template Distribution

## Objective
Create the `joshyorko/robot-templates` repository with all artifacts needed for RCC to pull predefined templates at build time and runtime.

## What RCC Expects

Based on `operations/initialize.go`, RCC expects these artifacts:

### 1. `templates.yaml` - Template Index
```yaml
date: "2024-01-15"           # Release date
hash: "sha256hashhere..."    # SHA256 of templates.zip
url: "https://github.com/joshyorko/robot-templates/releases/download/v1.0.0/templates.zip"
templates:
  standard: "Standard Robot Framework template"
  extended: "Extended Robot Framework template"
  python: "Basic Python template"
```

### 2. `templates.zip` - Bundle of All Templates
A zip file containing individual template zips:
```
templates.zip
├── standard.zip    # Contents of standard template
├── extended.zip    # Contents of extended template
└── python.zip      # Contents of python template
```

### 3. Individual Template Directories
Each template needs:
```
standard/
├── robot.yaml       # Task definitions (REQUIRED)
├── conda.yaml       # Environment dependencies (REQUIRED)
├── .gitignore       # Optional
└── tasks.robot      # Main task file (or task.py for Python)
```

---

## Repository Structure to Create

```
robot-templates/
├── README.md
├── templates/
│   ├── standard/
│   │   ├── robot.yaml
│   │   ├── conda.yaml
│   │   ├── tasks.robot
│   │   └── .gitignore
│   ├── extended/
│   │   ├── robot.yaml
│   │   ├── conda.yaml
│   │   ├── tasks.robot
│   │   ├── README.md
│   │   ├── keywords/
│   │   │   └── keywords.robot
│   │   ├── libraries/
│   │   │   ├── __init__.py
│   │   │   └── MyLibrary.py
│   │   ├── variables/
│   │   │   ├── __init__.py
│   │   │   └── variables.py
│   │   └── devdata/
│   │       └── env.json
│   └── python/
│       ├── robot.yaml
│       ├── conda.yaml
│       ├── task.py
│       └── .gitignore
├── scripts/
│   └── build-release.sh     # Script to build templates.zip + templates.yaml
└── .github/
    └── workflows/
        └── release.yaml     # CI workflow to build and publish releases
```

---

## Tasks for Creating the Repository

### Task 1: Clone and Setup Repository
The repo `joshyorko/robot-templates` already exists. Clone it and set up the directory structure above.

### Task 2: Create the 3 Template Directories
Copy and adapt templates from current RCC repo (`templates/*`), making these changes:

**For all templates:**
- Remove Robocorp doc links from `conda.yaml` comments
- Update any doc URLs to `https://github.com/joshyorko/rcc/blob/main/docs/`

**standard template** (`templates/standard/`):
- `robot.yaml` - Basic task config
- `conda.yaml` - Python + rpaframework deps
- `tasks.robot` - Minimal Robot Framework task
- `.gitignore`

**extended template** (`templates/extended/`):
- All of standard, plus:
- `README.md` - Rewrite to be generic (remove Control Room references)
- `keywords/keywords.robot` - Custom keywords example
- `libraries/MyLibrary.py` - Custom Python library example
- `variables/variables.py` - Variables example
- `devdata/env.json` - Update URL (remove Robocorp reference)

**python template** (`templates/python/`):
- `robot.yaml` - Basic task config for Python
- `conda.yaml` - Python deps
- `task.py` - Minimal Python task
- `.gitignore`

### Task 3: Create Build Script
`scripts/build-release.sh`:
```bash
#!/bin/bash
# Builds templates.zip and templates.yaml for release

VERSION=${1:-"v1.0.0"}
OUTPUT_DIR="dist"

mkdir -p "$OUTPUT_DIR"

# Create individual template zips
for template in templates/*/; do
  name=$(basename "$template")
  (cd "$template" && zip -r "../../$OUTPUT_DIR/$name.zip" .)
done

# Create combined templates.zip
(cd "$OUTPUT_DIR" && zip templates.zip *.zip && rm -f standard.zip extended.zip python.zip)

# Calculate SHA256
HASH=$(sha256sum "$OUTPUT_DIR/templates.zip" | cut -d' ' -f1)

# Generate templates.yaml
cat > "$OUTPUT_DIR/templates.yaml" << EOF
date: "$(date +%Y-%m-%d)"
hash: "$HASH"
url: "https://github.com/joshyorko/robot-templates/releases/download/$VERSION/templates.zip"
templates:
  standard: "Standard Robot Framework template"
  extended: "Extended Robot Framework template with libraries and keywords"
  python: "Basic Python automation template"
EOF

echo "Built: $OUTPUT_DIR/templates.zip"
echo "Built: $OUTPUT_DIR/templates.yaml"
echo "SHA256: $HASH"
```

### Task 4: Create GitHub Actions Release Workflow
`.github/workflows/release.yaml`:
```yaml
name: Release Templates

on:
  push:
    tags:
      - 'v*'

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build release artifacts
        run: |
          chmod +x scripts/build-release.sh
          ./scripts/build-release.sh ${{ github.ref_name }}

      - name: Create Release
        uses: softprops/action-gh-release@v1
        with:
          files: |
            dist/templates.zip
            dist/templates.yaml
```

### Task 5: Create README.md
Document how to:
- Use templates with RCC (`rcc robot initialize -t standard`)
- Add new templates
- Build locally
- Contribute

---

## Template File Contents Reference

### robot.yaml (required)
```yaml
tasks:
  Run all tasks:
    shell: python -m robot --report NONE --outputdir output --logtitle "Task log" tasks.robot

condaConfigFile: conda.yaml
artifactsDir: output
PATH:
  - .
PYTHONPATH:
  - .
ignoreFiles:
  - .gitignore
```

### conda.yaml (required)
```yaml
channels:
  - conda-forge

dependencies:
  - python=3.9.13
  - pip=22.1.2
  - pip:
    - rpaframework==15.6.0
```

### tasks.robot (for Robot Framework templates)
```robot
*** Settings ***
Documentation   Template robot main suite.

*** Tasks ***
Minimal task
    Log  Done.
```

### task.py (for Python templates)
```python
def main():
    print("Hello from Python template!")

if __name__ == "__main__":
    main()
```

---

## After robot-templates is Created

Once this repo exists with a v1.0.0 release, THEN modify RCC to:
1. Create `assets/yorko_settings.yaml` pointing to `https://github.com/joshyorko/robot-templates/releases/download/v1.0.0/templates.yaml`
2. Update the build to use the new settings
3. Update version check to use GitHub releases API
