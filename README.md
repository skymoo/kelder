# smol-claude: Claude Code in a smolvm guest

Claude Code runs inside a minimal smolvm VM (Debian, nested KVM). It can start
its own VMs (`testvm`) and containers (`podman`) there to run tests.

## Install

Needs, on the host: smolvm 1.32 or newer, KVM with nested virtualization, and
bash, flock and realpath (util-linux and coreutils).

```sh
make install                      # to ~/.local; make sure ~/.local/bin is on PATH
make install PREFIX=/usr/local    # or system-wide, as root
smol-claude-setup                 # create and provision the machine (safe to re-run)
```

`make uninstall` (with the same `PREFIX`) removes it again. The scripts also
run straight from a checkout (`bin/smol-claude`), or through a symlink to them.

```text
bin/smol-claude             host: attach a project directory and run claude in the guest
bin/smol-claude-setup       host: create and provision the guest
lib/smol.sh                 host: shared helpers; every smolvm CLI assumption lives here
guest/testvm                guest: throwaway QEMU/KVM VMs (installed to /usr/local/bin)
guest/containers-tsi.conf   guest: podman DNS workaround (see below)
```

`make install` puts `lib/` and `guest/` in `$PREFIX/share/smol-claude`.

## Usage

```sh
cd ~/git/app && smol-claude                  # attach ~/git/app and start Claude there
smol-claude -C ~/git/other                   # or name the directory
smol-claude -p "run the tests" | less        # non-interactive works too
```

The commands aren't called `cc` because that would shadow the C compiler; add
`alias cc=smol-claude` in your shell if you want the short name interactively.

One persistent machine serves every project. `smol-claude` attaches the
directory to the running machine (`smolvm machine mount add`) at the same path
it has on the host, and detaches it when the last session using it exits. Only
one directory is attached at a time: a session for a different directory is
refused while another is open (use `VM=<name>` for a second machine). A session
that crashes leaves its directory attached until the next run cleans it up.

Claude's state (login, settings, history, memory) is kept on the host in
`~/.local/share/smol-claude/claude` (override with `CLAUDE_STATE`), mounted at
`/var/lib/smol-claude` in the machine, with `CLAUDE_CONFIG_DIR` pointing there.
The machine itself is disposable: delete it and run `smol-claude-setup` to
rebuild, and nothing is lost. Changing `IMAGE` works the same way.

In the guest:

```sh
testvm run -- 'apt-get update && apt-get install -y nginx'   # fresh VM, destroyed after
testvm up web -p 8080:80 && testvm ssh web                   # keep one around
testvm cp ./build web:/opt/ && testvm down web
podman run --rm docker.io/library/debian:trixie ...          # use full image names
```

## Development

`make check` lints the scripts (needs shellcheck and python3).

## Quirks found while setting this up

**Nested VMs must boot with UEFI.** Under smolvm's nested KVM (AMD host), a
cloud image booted through BIOS GRUB resets the moment GRUB jumps into the
kernel. The same kernel boots fine when QEMU loads it directly, and the image
boots fine with OVMF. `testvm` always uses OVMF, so `ovmf` is a required package.

**UDP inside containers.** The guest has no real NIC: libkrun's TSI
(`tsi_hijack`) proxies every socket to the host. Inside a container network
namespace, a *connected* UDP socket can send only one datagram; later sends
fail with `EINVAL`. Unconnected `sendto` works, and the guest's own namespace
is unaffected. glibc sends DNS A and AAAA queries on one connected socket, so
container DNS broke (apt: "Temporary failure resolving"). The
`single-request-reopen` resolver option fixes DNS. Other UDP-heavy workloads in
containers (QUIC, statsd, syslog) may need `--network host`. This is a libkrun
bug, worth reporting upstream.

**Unverified smolvm assumptions** (all in `lib/smol.sh`): `machine status`
fails only for unknown machines; `machine start` on a running machine either
succeeds or fails harmlessly; `machine exec` takes `-i`/`-t` separately.

## License

Copyright (C) 2026 Adam Mercer. smol-claude is free software, licensed under
the GNU General Public License, version 3 or (at your option) any later
version. See [LICENSE](LICENSE).
