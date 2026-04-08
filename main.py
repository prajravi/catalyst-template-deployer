"""Catalyst Center Template Deployer
====================================
Deploys configuration templates to network devices via the Catalyst Center
REST API.  Only CSV-based batch deployments are supported; pass a CSV file
with the --input flag to trigger a deployment run.

Usage:
    python main.py --template "Project/TemplateName" --input devices.csv
    python main.py --template "Project/TemplateName" --input devices.csv --force
    python main.py --template "Project/TemplateName" --input devices.csv -v

CSV format:
    The first column must be named 'hostname'.
    All additional columns are forwarded as template parameters.

    Example (no extra parameters):
        hostname
        switch-floor1
        switch-floor2

    Example (with parameters):
        hostname,vlan_id,description
        switch-floor1,100,Finance VLAN
        switch-floor2,200,Engineering VLAN
"""

import csv
import datetime
import json
import logging
import sys
import time
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import urllib3  # bundled with dnacentersdk; not a separate install
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # suppress SSL warnings for self-signed certs

from dnacentersdk import api

from config import (
    CONTROLLER_API_VERSION,
    CONTROLLER_HOST,
    CONTROLLER_PASSWORD,
    CONTROLLER_PORT,
    CONTROLLER_USERNAME,
)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class PushTimeoutError(Exception):
    """Raised when a template push does not complete within the allowed window."""


class PushFailureError(Exception):
    """Raised when the controller returns an error during a template push."""


# ---------------------------------------------------------------------------
# Deployment helper (submit + poll until complete)
# ---------------------------------------------------------------------------


def submit_and_poll(session: Any, push_payload: dict) -> dict:
    """Submit a template deployment and block until the job finishes.

    Sends the deployment payload to the controller, extracts the resulting
    job ID, and polls the deployment status endpoint every 10 seconds until
    the job reports a non-empty end time.  Raises PushTimeoutError if the
    job has not completed within 30 minutes.

    Args:
        session (Any): An authenticated Catalyst Center API session object.
        push_payload (dict): The fully-formed deployment request body,
            including templateId, targetInfo, and forcePushTemplate flag.

    Returns:
        dict: The final deployment status response returned by the controller.

    Raises:
        PushFailureError: If the controller rejects the deployment request or
            if polling the status endpoint raises an exception.
        PushTimeoutError: If the deployment job does not finish within the
            configured timeout window (30 minutes).
    """
    try:
        push_response = session.configuration_templates.deploy_template(
            payload=push_payload
        )
        print("")
    except Exception as submit_err:
        raise PushFailureError(
            f"Error submitting deployment request: {submit_err}"
        ) from submit_err

    # The deploymentId field encodes the job ID and optional error hints as
    # a colon-separated string, e.g. "Task:<scope>:<uuid>" — extract the UUID.
    raw_job_field = push_response["deploymentId"]
    job_id = raw_job_field.split(":")[-1].strip()

    # A response string containing "already deployed" indicates the controller
    # considers the template already applied and the push was rejected.
    if "already deployed" in job_id:
        raise PushFailureError(
            f"Deployment rejected by controller: {job_id}"
        )

    # "nonApp" in the scope segment signals a device-type mismatch.
    scope_segment = raw_job_field.split(":")[1].strip()
    if "nonApp" in scope_segment:
        raise PushFailureError(
            f"Device type incompatibility detected: {raw_job_field}"
        )

    print(f"Deployment submitted (job ID: {job_id}). Waiting for completion...")

    poll_interval_secs = 10
    max_wait_secs = 1800  # 30 minutes
    start_ts = time.time()

    while True:
        try:
            status_response = (
                session.configuration_templates.get_template_deployment_status(
                    deployment_id=job_id
                )
            )
        except Exception as poll_err:
            raise PushFailureError(
                f"Error polling deployment status for job {job_id}: {poll_err}"
            ) from poll_err

        # A non-empty endTime signals the job has completed (success or failure).
        if status_response.get("endTime", "") != "":
            break

        if time.time() > start_ts + max_wait_secs:
            raise PushTimeoutError(
                f"Job {job_id} did not finish within {max_wait_secs} seconds."
            )

        print(f"Job {job_id} still in progress. Retrying in {poll_interval_secs}s...")
        time.sleep(poll_interval_secs)

    return status_response


# ---------------------------------------------------------------------------
# Template catalog helpers
# ---------------------------------------------------------------------------


def retrieve_template_catalog(session: Any) -> list[dict[str, Any]]:
    """Retrieve metadata for all committed templates from the controller.

    Calls the 'gets_the_templates_available' endpoint with un_committed=False
    so that only committed (deployable) templates are returned.

    Args:
        session (Any): An authenticated Catalyst Center API session object.

    Returns:
        list[dict[str, Any]]: A list of template metadata dictionaries.

    Raises:
        RuntimeError: If the API call fails for any reason.
    """
    try:
        catalog = session.configuration_templates.gets_the_templates_available(
            un_committed=False
        )
    except Exception as catalog_err:
        raise RuntimeError(
            f"Failed to retrieve template catalog: {catalog_err}"
        ) from catalog_err

    return catalog


def resolve_template_id(
    qualified_name: str, catalog: list[dict[str, Any]]
) -> str:
    """Resolve a 'Project/Template' name to the UUID of its latest version.

    Iterates through the catalog to find the entry matching both the project
    name and template name, then sorts the available committed versions by
    versionTime descending and returns the UUID of the newest one.

    Args:
        qualified_name (str): Template in "ProjectName/TemplateName" format.
        catalog (list[dict[str, Any]]): Template catalog returned by
            retrieve_template_catalog().

    Returns:
        str: UUID of the latest committed version of the specified template.

    Raises:
        ValueError: If qualified_name is not in 'Project/Template' format, or
            if no matching template can be found in the catalog.
    """
    try:
        proj_name, tpl_name = qualified_name.split("/", 1)
    except ValueError as parse_err:
        raise ValueError(
            f"Template name must be in 'Project/Template' format, "
            f"got: '{qualified_name}'"
        ) from parse_err

    print(f"Searching catalog for: {proj_name}/{tpl_name}")

    for entry in catalog:
        try:
            if (
                entry.get("projectName") == proj_name
                and entry.get("name") == tpl_name
            ):
                # Sort version history newest-first by versionTime (epoch ms)
                sorted_versions = sorted(
                    entry["versionsInfo"],
                    key=lambda v: v["versionTime"],
                    reverse=True,
                )
                latest = sorted_versions[0]
                last_updated = datetime.datetime.fromtimestamp(
                    latest["versionTime"] / 1000
                ).replace(microsecond=0)

                print(
                    f'\nFound "{proj_name}/{tpl_name}"\n'
                    f'  Author      : {latest.get("author", "N/A")}\n'
                    f'  Description : {latest.get("description", "N/A")}\n'
                    f'  Last updated: {last_updated}\n'
                )
                return latest["id"]

        except (KeyError, IndexError) as entry_err:
            logging.warning("Skipping malformed catalog entry: %s", entry_err)

    raise ValueError(
        f"Template '{qualified_name}' was not found in the catalog."
    )


# ---------------------------------------------------------------------------
# Device inventory helpers
# ---------------------------------------------------------------------------


def fetch_device_page(session: Any, page_offset: int) -> dict:
    """Retrieve a single page of up to 500 devices from the controller.

    This is a helper function called by build_device_map() via a thread pool.
    Each invocation fetches at most 500 device records starting at page_offset.

    Args:
        session (Any): An authenticated Catalyst Center API session object.
        page_offset (int): The 1-based record offset for this page (e.g. 1,
            501, 1001 …).

    Returns:
        dict: Raw API response whose 'response' key holds a list of device
            detail dictionaries.

    Raises:
        RuntimeError: If the API call for this page fails.
    """
    try:
        return session.devices.get_device_list(limit=500, offset=page_offset)
    except Exception as page_err:
        raise RuntimeError(
            f"Failed to fetch device page at offset {page_offset}: {page_err}"
        ) from page_err


def build_device_map(session: Any) -> dict[str, str]:
    """Build a hostname-to-UUID lookup map for all devices in the inventory.

    Fetches the total device count, then uses a thread pool to retrieve all
    inventory pages concurrently.  Returns a dictionary keyed by short hostname
    (domain suffix stripped) mapping to the device's management UUID.

    Args:
        session (Any): An authenticated Catalyst Center API session object.

    Returns:
        dict[str, str]: Mapping of short hostnames to device UUIDs.
            Example: {"switch-floor1": "aabbccdd-1234-5678-..."}

    Raises:
        RuntimeError: If the device count or any inventory page cannot be
            retrieved.
    """
    all_device_records: list[dict] = []
    hostname_uuid_map: dict[str, str] = {}

    try:
        total_count: int = session.devices.get_device_count()["response"]
    except Exception as count_err:
        raise RuntimeError(
            f"Could not retrieve device count from controller: {count_err}"
        ) from count_err

    # Pages are 500 records each; offsets are 1-based
    page_offsets = range(1, total_count + 1, 500)

    # Fetch all pages concurrently to minimise wall-clock time for large fleets
    try:
        with ThreadPoolExecutor() as pool:
            page_futures = [
                pool.submit(fetch_device_page, session, offset)
                for offset in page_offsets
            ]
            for fut in page_futures:
                page_data = fut.result()
                all_device_records.extend(page_data["response"])
    except Exception as fetch_err:
        raise RuntimeError(
            f"Error while fetching device inventory pages: {fetch_err}"
        ) from fetch_err

    # Build the hostname→UUID map, using only the short hostname
    for record in all_device_records:
        try:
            short_name = record["hostname"].split(".")[0]
            uid = record["id"]
            if short_name and uid:
                hostname_uuid_map[short_name] = uid
        except (KeyError, AttributeError):
            # Skip any record that is missing hostname or id fields
            continue

    return hostname_uuid_map


# ---------------------------------------------------------------------------
# CSV batch deployment
# ---------------------------------------------------------------------------


def run_batch_from_csv(
    session: Any,
    tpl_id: str,
    input_file: str,
    force_push: bool = False,
) -> dict:
    """Deploy a template to multiple devices defined in a CSV file.

    Reads the CSV row by row.  The mandatory 'hostname' column
    identifies the target device; all other columns are forwarded as template
    parameter key-value pairs.  Devices not found in the controller inventory
    are skipped with a warning.

    Args:
        session (Any): An authenticated Catalyst Center API session object.
        tpl_id (str): UUID of the committed template to deploy.
        input_file (str): Absolute or relative path to the input CSV file.
        force_push (bool): When True, re-deploys even if the template is
            already applied to a device.  Defaults to False.

    Returns:
        dict: Final deployment status response from submit_and_poll().

    Raises:
        FileNotFoundError: If the CSV file path does not exist on disk.
        ValueError: If the CSV is missing the required 'hostname' column.
        PushFailureError: If the controller rejects the deployment request.
        PushTimeoutError: If the deployment job exceeds the timeout window.
    """
    target_list: list[dict] = []

    # Pre-build the full hostname→UUID map so we only query the inventory once
    device_map = build_device_map(session)

    try:
        with open(input_file, newline="", encoding="utf-8") as csv_fh:
            # skipinitialspace trims spaces that immediately follow a comma delimiter
            row_reader = csv.DictReader(csv_fh, skipinitialspace=True)

            # Validate that the mandatory column is present before processing rows
            if "hostname" not in (row_reader.fieldnames or []):
                raise ValueError(
                    "The CSV file must contain a 'hostname' column."
                )

            for row in row_reader:
                # Strip all leading/trailing whitespace from every key and value.
                # None keys can appear when a row has more columns than the header;
                # they are discarded.  None values are normalised to empty strings.
                row = {
                    k.strip(): (v.strip() if v is not None else "")
                    for k, v in row.items()
                    if k is not None
                }

                # Extract and normalise the target hostname
                raw_hostname = row.pop("hostname", "").strip()

                # Use only the short hostname (no domain suffix) for inventory lookup
                short_hostname = raw_hostname.split(".")[0]

                target_uuid = device_map.get(short_hostname)
                if not target_uuid:
                    print(
                        f"[SKIP] '{raw_hostname}' not found in inventory — skipping."
                    )
                    continue

                # All remaining CSV columns become template parameter overrides
                tpl_params = {k: v for k, v in row.items() if k}

                print(f"[QUEUE] {raw_hostname} | params: {tpl_params}")

                target_list.append(
                    {
                        "id": target_uuid,
                        "type": "MANAGED_DEVICE_UUID",
                        "params": tpl_params,
                        "resourceParams": [
                            {
                                "type": "MANAGED_DEVICE_UUID",
                                "scope": "RUNTIME",
                                "value": target_uuid,
                            }
                        ],
                    }
                )

    except FileNotFoundError as fnf_err:
        raise FileNotFoundError(
            f"Input CSV file not found: '{input_file}'"
        ) from fnf_err

    if not target_list:
        print("No valid targets were found in the CSV file. Nothing to deploy.")
        sys.exit(0)

    deployment_payload = {
        "templateId": tpl_id,
        "forcePushTemplate": force_push,
        "copyingConfig": True,
        "targetInfo": target_list,
    }

    return submit_and_poll(session, deployment_payload)


# ---------------------------------------------------------------------------
# Template listing
# ---------------------------------------------------------------------------


def list_available_templates(session: Any) -> None:
    """Fetch and print all committed templates grouped by project.

    Retrieves the full template catalog and prints each entry as
    'ProjectName/TemplateName', sorted alphabetically.  This is the
    default behaviour when main.py is run without --template/--input.

    Args:
        session (Any): An authenticated Catalyst Center API session object.

    Raises:
        RuntimeError: If the template catalog cannot be retrieved.
    """
    try:
        catalog = retrieve_template_catalog(session)
    except RuntimeError as catalog_err:
        raise RuntimeError(
            f"Could not retrieve template catalog: {catalog_err}"
        ) from catalog_err

    if not catalog:
        print("No committed templates found on the controller.")
        return

    try:
        sorted_entries = sorted(
            [
                "{}/{}".format(entry["projectName"], entry["name"])
                for entry in catalog
            ]
        )
    except KeyError as key_err:
        raise RuntimeError(
            f"Unexpected catalog entry format: {key_err}"
        ) from key_err

    print(
        f"Available templates on '{CONTROLLER_HOST}' "
        f"({len(sorted_entries)} found):\n"
    )
    for tpl_path in sorted_entries:
        print(f"  {tpl_path}")


def inspect_template(session: Any, tpl_id: str) -> None:
    """Fetch and display the body and parameter list of a specific template.

    Retrieves the full template details from the controller using the supplied
    UUID and prints:
      - The raw CLI/Jinja template body.
      - A table of all parameters, indicating which are free-form inputs and
        which have automatic bindings (e.g. sourced from device inventory).

    Args:
        session (Any): An authenticated Catalyst Center API session object.
        tpl_id (str): UUID of the committed template version to inspect.

    Raises:
        RuntimeError: If the API call to fetch template details fails.
    """
    try:
        raw = session.configuration_templates.get_template_details(
            template_id=tpl_id
        )
    except Exception as detail_err:
        raise RuntimeError(
            f"Failed to fetch template details for ID '{tpl_id}': {detail_err}"
        ) from detail_err

    # The SDK may wrap the payload in a 'response' envelope depending on version
    details = raw.get("response", raw) if isinstance(raw, dict) else raw

    try:
        print("\n--- Template Body ---")
        print(details.get("templateContent", "(no content)"))

        all_params = details.get("templateParams", [])

        # Separate free-form inputs from auto-bound parameters
        required_inputs = [
            p["parameterName"]
            for p in all_params
            if not p.get("binding")
        ]
        bound_params = [
            "{} (bound: {}.{})".format(
                p["parameterName"],
                json.loads(p["binding"])["source"],
                json.loads(p["binding"])["entity"],
            )
            for p in all_params
            if p.get("binding")
        ]

        print("\n--- Required CSV Parameters ---")
        if required_inputs:
            for param_name in required_inputs:
                print(f"  {param_name}")
            # Show an example CSV header row for convenience
            header_row = "hostname," + ",".join(required_inputs)
            print(f"\n  Example CSV header row:")
            print(f"  {header_row}")
        else:
            print("  (none — template has no free-form input parameters)")

        if bound_params:
            print("\n--- Auto-Bound Parameters (no CSV input needed) ---")
            for bp in bound_params:
                print(f"  {bp}")

    except (KeyError, json.JSONDecodeError, TypeError) as parse_err:
        raise RuntimeError(
            f"Could not parse template details: {parse_err}"
        ) from parse_err


# ---------------------------------------------------------------------------
# Result display
# ---------------------------------------------------------------------------


def display_deployment_summary(outcome: dict) -> None:
    """Print a human-readable summary of a completed deployment job.

    Extracts the project name, template name, version, and per-device status
    lines from the outcome dictionary and prints them to stdout.  Falls back
    to pretty-printed JSON if the expected keys are absent.

    Args:
        outcome (dict): The deployment status response returned by
            submit_and_poll().
    """
    try:
        print("\n--- Deployment Summary ---")
        print(
            "Template : {}/{} (v{})".format(
                outcome["projectName"],
                outcome["templateName"],
                outcome["templateVersion"],
            )
        )
        print(f"Job ID   : {outcome['deploymentId']}")
        print("")

        for node in outcome.get("devices", []):
            print(
                "  {} | {} | {}".format(
                    node.get("ipAddress", "N/A"),
                    node.get("status", "N/A"),
                    node.get("detailedStatusMessage", ""),
                )
            )
    except (KeyError, TypeError) as display_err:
        logging.warning("Could not parse deployment summary: %s", display_err)
        # Fall back to raw JSON output so no information is lost
        print(json.dumps(outcome, indent=2))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    arg_parser = ArgumentParser(
        description=(
            "Deploy Catalyst Center configuration templates to network devices "
            "via a CSV input file."
        )
    )

    arg_parser.add_argument(
        "--template",
        type=str,
        required=False,
        default=None,
        help=(
            "Fully-qualified template name in 'Project/Template' format. "
            "Omit to list all available templates. "
            "Use this to inspect a template's body and parameters without deploying by omitting --input."
        ),
    )

    arg_parser.add_argument(
        "--input",
        type=str,
        required=False,
        default=None,
        metavar="FILE.csv",
        help=(
            "Path to the CSV file containing device hostnames and optional "
            "template parameters.  Must have a '.csv' extension. "
            "Required when --template is provided."
        ),
    )

    arg_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-deploy the template even if it is already applied to a device.",
    )

    arg_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG-level) logging output.",
    )

    cli_args = arg_parser.parse_args()

    # Validate argument combinations before touching the network
    if cli_args.input and not cli_args.template:
        print("Error: --template is required when --input is specified.")
        sys.exit(1)

    # Reject non-CSV inputs early to prevent confusing errors later
    if cli_args.input and not cli_args.input.lower().endswith(".csv"):
        print("Error: --input must point to a CSV file (file name must end in .csv).")
        sys.exit(1)

    # Set up root logger; DEBUG when -v is given, otherwise WARNING only
    log_level = logging.DEBUG if cli_args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # -----------------------------------------------------------------------
    # Connect to the Catalyst Center controller
    # -----------------------------------------------------------------------
    try:
        controller_session = api.DNACenterAPI(
            base_url=f"https://{CONTROLLER_HOST}:{CONTROLLER_PORT}",
            username=CONTROLLER_USERNAME,
            password=CONTROLLER_PASSWORD,
            version=CONTROLLER_API_VERSION,
            verify=False,  # Disable SSL verification for self-signed certificates
        )
    except Exception as conn_err:
        print(
            f"Failed to connect to Catalyst Center at "
            f"'{CONTROLLER_HOST}:{CONTROLLER_PORT}': {conn_err}"
        )
        sys.exit(1)

    # -----------------------------------------------------------------------
    # No arguments — list all available templates and exit
    # -----------------------------------------------------------------------
    if not cli_args.template:
        try:
            list_available_templates(controller_session)
        except RuntimeError as list_err:
            print(f"Error listing templates: {list_err}")
            sys.exit(1)
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Resolve the template name to a deployable UUID (needed for both
    # inspection and deployment paths)
    # -----------------------------------------------------------------------
    try:
        tpl_catalog = retrieve_template_catalog(controller_session)
        tpl_id = resolve_template_id(cli_args.template, tpl_catalog)
    except (ValueError, RuntimeError) as tmpl_err:
        print(f"Template error: {tmpl_err}")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # --template only (no --input) — inspect template body + params, then exit
    # -----------------------------------------------------------------------
    if not cli_args.input:
        try:
            inspect_template(controller_session, tpl_id)
        except RuntimeError as inspect_err:
            print(f"Error inspecting template: {inspect_err}")
            sys.exit(1)
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Run the batch deployment from the CSV file
    # -----------------------------------------------------------------------
    try:
        deployment_result = run_batch_from_csv(
            session=controller_session,
            tpl_id=tpl_id,
            input_file=cli_args.input,
            force_push=cli_args.force,
        )
        display_deployment_summary(deployment_result)

    except (FileNotFoundError, ValueError) as input_err:
        print(f"Input error: {input_err}")
        sys.exit(1)
    except PushFailureError as push_err:
        print(f"Deployment failed: {push_err}")
        sys.exit(1)
    except PushTimeoutError as timeout_err:
        print(f"Deployment timed out: {timeout_err}")
        sys.exit(1)
