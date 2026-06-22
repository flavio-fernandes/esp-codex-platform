# GitHub setup

Keep the repo local until the devcontainer and validation path pass.

Before pushing:

```bash
git status --short
git log --oneline --decorate -5
git remote -v
git ls-files
git status --ignored --short
git diff --check
```

Do not push if any of these are tracked:

- `secrets.yaml`
- `.env` or `.env.*`
- private keys
- firmware binaries such as `*.bin`, `*.elf`, `*.factory.bin`, or `*.ota.bin`
- `artifacts/`
- `.esphome/`
- local workbench config such as `config/workbench.env`

Create a private repo by default unless the user explicitly chooses otherwise.
Make a repo public only after running `docs/public-release-checklist.md`.
Do not push to GitHub without explicit approval.
