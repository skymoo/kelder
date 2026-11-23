# kelder: Claude Code in a QEMU/KVM guest

Claude Code runs inside a minimal virtual machine (Debian 13 under QEMU,
with nested KVM), as a user with your uid. It can start its own VMs
(`testvm`) and containers (`podman`) there to run tests.

## Install

Needs, on an x86_64 Linux host: `qemu-system-x86_64`, `qemu-img`,
`virtiofsd`, `ssh`, `ssh-keygen`, one of `genisoimage`, `xorrisofs` or
`mkisofs`, access to `/dev/kvm` with nested virtualization, and Python 3.11
or newer. kelder itself has no Python dependencies.

```sh
uv tool install kelder            # puts kelder on PATH
kelder self setup                 # create and provision the machine (safe to re-run)
```

`uv tool uninstall kelder` removes it again. From a checkout, `uv tool
install .` installs that copy instead, and `uv run kelder` runs it without
installing.

## Usage

```sh
cd ~/git/app && kelder                  # attach ~/git/app and start Claude there
kelder self -C ~/git/other              # or name the directory
cd ~/git/app && kelder self -a ../lib   # also give Claude ~/git/lib
kelder -p "run the tests" | less        # non-interactive works too
kelder self shell                       # a shell in the guest instead of Claude
```

Only arguments after `self` (which must come first) are `kelder`'s own;
everything else is passed unchanged to `claude` in the guest.

## Documentation

The documentation is in the `docs/` directory of the repository:

- `docs/usage.md`: the commands, sessions, and where Claude's state is kept
- `docs/configuration.md`: `.kelder.toml`, your own `config.toml`, and
  environment variables
- `docs/tools.md`: mise, uv, `testvm` and `podman` in the guest
- `docs/machine.md`: how it is run, its files on the host, and testing the
  host's QEMU
- `docs/development.md`: source layout, tests and releasing

## License

Copyright (C) 2026 Adam Mercer. kelder is free software, licensed under
the GNU General Public License, version 3 or (at your option) any later
version. See `LICENSE`.
