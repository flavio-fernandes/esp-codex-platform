# Local environment settings

This directory is the obvious home for setup-specific values such as workbench
IP addresses, SSH users, and RFC2217 monitor ports.

Committed files:

- `workbench.env.example`: safe placeholder values and documentation.

Ignored local files:

- `workbench.env`: real values for this machine/network.

To configure a checkout:

```bash
cp config/workbench.env.example config/workbench.env
```

Then edit `config/workbench.env`.

The project tools automatically source `config/workbench.env` when it exists.
Environment variables passed on the command line still override the values in
that file.
