"""Example configuration for the Catalyst Center Template Deployer.

Copy this file to config.py and replace the placeholder values with your
actual Catalyst Center connection details, OR export the corresponding
environment variables before running main.py.

Environment variables take precedence over the default values below.

Example (bash / zsh):
    export CONTROLLER_HOST="10.0.0.1"
    export CONTROLLER_USERNAME="admin"
    export CONTROLLER_PASSWORD="SuperSecret99!"
    export CONTROLLER_PORT="443"
    export CONTROLLER_API_VERSION="2.3.7.9"
"""

import os

# ---------------------------------------------------------------------------
# Required — update these values for your environment
# ---------------------------------------------------------------------------

# Hostname or IP address of the Catalyst Center controller
CONTROLLER_HOST: str = os.environ.get("CONTROLLER_HOST", "10.0.0.1")

# HTTPS port for the Catalyst Center API (almost always 443)
CONTROLLER_PORT: str | int = os.environ.get("CONTROLLER_PORT", 443)

# API login credentials
CONTROLLER_USERNAME: str = os.environ.get("CONTROLLER_USERNAME", "admin")
CONTROLLER_PASSWORD: str = os.environ.get("CONTROLLER_PASSWORD", "ChangeMe123!")

# ---------------------------------------------------------------------------
# Optional — change only if your controller runs a non-default API version
# ---------------------------------------------------------------------------

# Catalyst Center REST API version (must match a version accepted by dnacentersdk)
# See: https://dnacentersdk.readthedocs.io/en/latest/api/intro.html
CONTROLLER_API_VERSION: str = os.environ.get("CONTROLLER_API_VERSION", "2.3.7.9")
