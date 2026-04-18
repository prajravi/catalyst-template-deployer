# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please do **not** create a public GitHub issue. Instead, please report it responsibly:

1. **Email**: Send details to the project maintainer via GitHub (check the repository for contact info)
2. **Include**: Detailed description of the vulnerability, affected versions, and any proof-of-concept

We take security seriously and will:
- Acknowledge your report within 48 hours
- Work on a fix and release a patched version
- Credit you in the security advisory (unless you prefer anonymity)

## Security Considerations

### Credential Management
- **Never** hard-code credentials in `config.py` or any source files
- Use environment variables for all sensitive configuration:
  - `CONTROLLER_HOST`
  - `CONTROLLER_USERNAME`
  - `CONTROLLER_PASSWORD`
  - `CONTROLLER_PORT`
  - `CONTROLLER_API_VERSION`
- Add `config.py` to `.gitignore` to prevent accidental commits

### SSL/TLS
- In production environments, ensure SSL certificate validation is enabled
- The current implementation disables SSL verification for lab environments only—re-enable for production use

### Dependencies
- This project depends on `dnacentersdk`—keep it updated to receive security patches
- Run `pip install --upgrade dnacentersdk` regularly

## Best Practices for Users

1. Store credentials in a secure vault or use environment variable management tools
2. Review template content before deployment to large device populations
3. Use the `--input` CSV file feature safely—validate CSV data before deployment
4. Enable verbose logging (`-v`) during initial deployments for auditing

## License

This project is released under the [MIT License](./LICENSE).
