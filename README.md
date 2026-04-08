# Catalyst Center Template Deployer

A lightweight Python script for deploying configuration templates to network
devices via the Catalyst Center REST API.  Batch deployments are driven by a
simple CSV file, making it easy to roll out the same template across many
devices in a single run.

---

## Features

- **Template listing** — run without arguments to list all committed templates available on the controller.
- **Template inspection** — pass only `--template` to view the template body, required CSV parameters, and auto-bound parameters.
- **CSV-driven batch deployment** — feed a CSV file with device hostnames and optional per-device template parameters via `--input`.
- **Concurrent inventory fetch** — uses a thread pool to retrieve large device inventories quickly.
- **Automatic retry / polling** — polls the deployment job until it completes or times out (30-minute window).
- **Whitespace-tolerant CSV parsing** — strips leading/trailing spaces from all keys and values; handles extra spaces after commas.
- **Self-signed certificate support** — SSL verification is disabled for lab environments.
- **Clear error reporting** — distinct exit messages for connection failures, missing templates, bad input files, deployment rejections, and timeouts.
- **Single-file design** — everything lives in `main.py`; no package installation required beyond the dependencies listed below.

---

## Requirements

- Python 3.10 or later
- [`dnacentersdk`](https://pypi.org/project/dnacentersdk/) — the official Catalyst Center Python SDK

Install the dependency:

```bash
pip install dnacentersdk
```

---

## Configuration

Configuration is loaded from `config.py`.  The quickest way to get started is
to copy the provided example file:

```bash
cp config_example.py config.py
```

Then open `config.py` and set the following values (or export the corresponding
environment variables — environment variables take precedence):

| Variable | Environment variable | Description |
|---|---|---|
| `CONTROLLER_HOST` | `CONTROLLER_HOST` | Hostname or IP of your Catalyst Center |
| `CONTROLLER_PORT` | `CONTROLLER_PORT` | API port (default: `443`) |
| `CONTROLLER_USERNAME` | `CONTROLLER_USERNAME` | API login username |
| `CONTROLLER_PASSWORD` | `CONTROLLER_PASSWORD` | API login password |
| `CONTROLLER_API_VERSION` | `CONTROLLER_API_VERSION` | SDK API version (default: `2.3.7.9`) |

**Example — exporting environment variables (bash / zsh):**

```bash
export CONTROLLER_HOST="10.10.20.85"
export CONTROLLER_USERNAME="admin"
export CONTROLLER_PASSWORD="MySecretPass!"
export CONTROLLER_PORT="443"
export CONTROLLER_API_VERSION="2.3.7.9"
```

> **Note:** `CONTROLLER_API_VERSION` must be a version string accepted by the
> `dnacentersdk` package. Refer to the
> [dnacentersdk documentation](https://dnacentersdk.readthedocs.io/en/latest/api/intro.html)
> for the list of supported versions.

---

## Usage

The script has three modes depending on the arguments provided:

### 1. List all available templates

Run with no arguments to connect to the controller and print every committed
template, sorted alphabetically as `Project/Template`.

```bash
python main.py
```

**Example output:**
```
Available templates on '10.10.20.85' (4 found):

  Campus-LAN/Configure-Access-VLAN
  Campus-LAN/Configure-Trunk
  PrajAutomates/shut_multiple_interfaces
  WAN/BGP-Peer-Config
```

---

### 2. Inspect a template

Pass only `--template` (without `--input`) to view the template's CLI body,
the list of parameters that must be supplied via CSV, and any parameters that
are auto-bound by the controller.

```bash
python main.py --template "Project/TemplateName"
```

**Example:**
```bash
python main.py --template "PrajAutomates/shut_multiple_interfaces"
```

**Example output:**
```
--- Template Body ---
interface $interface
 shutdown

--- Required CSV Parameters ---
  interface

  Example CSV header row:
  hostname,interface

--- Auto-Bound Parameters (no CSV input needed) ---
  (none)
```

---

### 3. Deploy to devices via CSV

Provide both `--template` and `--input` to run a batch deployment.  The script
resolves each hostname in the CSV to a device UUID, assembles the payload, and
submits a single bulk deployment request.

```bash
python main.py --template "Project/TemplateName" --input devices.csv
```

**Options:**

| Argument | Required | Description |
|---|---|---|
| `--template` | Yes | Fully-qualified template name in `Project/Template` format |
| `--input` | Yes | Path to the input CSV file (must end in `.csv`) |
| `--force` | No | Re-deploy even if the template is already applied to a device |
| `-v` / `--verbose` | No | Enable DEBUG-level logging output |

**Examples:**

```bash
# Basic batch deployment
python main.py --template "Campus-LAN/Configure-Access-VLAN" --input site-a.csv

# Force re-apply template
python main.py --template "WAN/BGP-Peer-Config" --input routers.csv --force

# Verbose output for troubleshooting
python main.py --template "Campus-LAN/Configure-Access-VLAN" --input site-a.csv -v
```

---

## CSV File Format

The `--input` file must be a **comma-separated values (CSV)** file:

1. The first row must be a header row.
2. One column **must** be named `hostname`. Its value identifies the target
   device. The domain suffix is stripped automatically, so both `switch-floor1`
   and `switch-floor1.example.net` are accepted.
3. Every additional column is treated as a template parameter name, and the
   corresponding cell value is passed to the template for that device.
4. Extra whitespace around values and after commas is stripped automatically.

### Example — no template parameters

```csv
hostname
switch-floor1
switch-floor2
```

### Example — with template parameters

```csv
hostname,vlan_id,description
switch-floor1,100,Finance VLAN
switch-floor2,200,Engineering VLAN
router-core,300,Core Network
```

> **Tip:** Run `python main.py --template "Project/Template"` first to see
> exactly which columns your CSV needs for a given template.

---

## Project Structure

```
catalyst-template-deployer/
├── main.py            # Single-file application (all logic + CLI entry point)
├── config.py          # Active configuration (create from config_example.py)
├── config_example.py  # Template configuration file with documented defaults
└── README.md          # This file
```

---

## How It Works

1. **Connect** — establishes an authenticated session with the Catalyst Center using credentials from `config.py`.
2. **List** *(no args)* — retrieves and prints the full committed template catalog.
3. **Inspect** *(`--template` only)* — fetches full template details and displays the body, required parameters, and bindings.
4. **Deploy** *(`--template` + `--input`)* — resolves the template UUID, builds a hostname→UUID device map (fetched concurrently), parses the CSV, and submits a bulk deployment request.
5. **Poll** — checks the deployment job status every 10 seconds until complete or the 30-minute timeout is reached.
6. **Report** — prints a per-device status summary.

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success (list, inspect, or deployment completed) |
| `1` | Fatal error — connection failure, bad input, deployment rejected, or timed out |

---

## Security Notes

- Never hard-code passwords in `config.py`. Use environment variables (`CONTROLLER_PASSWORD`) instead, especially in shared or version-controlled environments.
- Add `config.py` to your `.gitignore` to avoid accidentally committing credentials.
