# The machine

The machine is a Debian 13 cloud image run by QEMU with KVM as your own
user, using what the host's distribution packages: virtiofsd shares the
attached directories and sessions go over ssh to a port on localhost.

- Sessions run as the guest user `kelder`, not root. It has your uid and
  gid, so files in attached directories belong to the same user on both
  sides, and it may `sudo` without a password. `podman` is rootless
  unless run with `sudo`. Run as root, kelder uses root in the guest too.
- It gets half the host's memory, up to 8 GiB, up to 4 CPUs and a 20 GB
  disk. Memory the guest has used is not returned to the host until the
  machine stops.
- Each attached directory takes a PCIe port, and those are fixed when the
  machine starts. A session that boots the machine gets as many as it
  attaches. One that finds it running with too few, as after a session
  that was killed, restarts it first (`kelder: restarting machine ...`);
  that only happens when no other session is in it.

## Files on the host

The machine's disk, ssh key and cloud-init seed are in
`~/.local/share/kelder/machines/<name>`, and the image it was copied from
in `~/.cache/kelder/images`. While it runs, its console log and one log
for each attached directory's virtiofsd are in
`$XDG_RUNTIME_DIR/kelder/<name>/qemu`. `kelder self list` shows the
machines there are, and `kelder self delete <name>` removes one.

## Testing the host's QEMU

After the host's QEMU or virtiofsd has been upgraded, run `kelder self
smoketest` from a project directory. It creates a throwaway `kelder-test`
machine (`VM=<name>` picks another name), provisions it as `kelder self
setup` would, runs the guest's `smoketest` in it with the current directory
attached, and deletes the machine if everything passed. The guest script
checks the session user, `sudo`, the ownership of files it creates in the
attached directory, UDP and DNS in a container, and that `testvm` boots a
nested VM; each line is PASS or FAIL.

A machine that fails is kept for inspection, and the command that removes
it is printed.

## Quirks

**Nested VMs boot with UEFI.** `testvm` always boots with OVMF, so `ovmf`
is a required package in the guest. It was chosen when a cloud image
booted through BIOS GRUB reset the moment GRUB jumped into the kernel
under nested KVM (AMD host), in the smolvm guest kelder used before;
whether BIOS would work in today's machine has not been tried.
