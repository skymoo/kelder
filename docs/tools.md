# Tools

## mise

Tools Debian doesn't package, or packages too old, come from
[mise](https://mise.jdx.dev), which every machine has. A project lists
them in its own `mise.toml`, the same file mise reads on the host and in
CI:

```toml
[tools]
zig = "0.15"
rust = "1.91"
```

- `kelder self setup`, run from the project, attaches it and runs `mise
  install`. As with `packages`, only setup installs: a session in a
  machine that lacks one of the project's tools says so and starts
  anyway, and a shim for a tool that isn't installed fails, or runs
  Debian's copy if there is one, rather than downloading it mid-session
  (`MISE_AUTO_INSTALL` and `MISE_NOT_FOUND_AUTO_INSTALL` are `false`).
- Sessions have mise's shims (`~/.local/share/mise/shims`) first on
  `PATH`. A shim picks the version from the directory it is run in, so
  projects sharing a machine can ask for different versions, and
  `command -v` finds a shim even where the tool isn't installed (`mise
  which` tells).
- The directories a session attaches are trusted
  (`MISE_TRUSTED_CONFIG_PATHS`), so a config with `[env]` or templates
  works without `mise trust`.
- mise comes from its Debian repository, which setup and the weekly
  update add if it is missing, so the package upgrade keeps it current.
  The tools themselves stay at the versions the project asks for.
- Debian packages a tool needs (a C compiler for Rust, say) go in
  `packages` in `.kelder.toml`.

## uv

The guest has `uv`, `make` and `shellcheck` for running a project's own
checks. The attached directory is shared with the host, whose `.venv` points
at the host's Python, so sessions set `UV_PROJECT_ENVIRONMENT` to `.venv-`
followed by the machine's name (`.venv-kelder` in the default machine):
`uv run` in the guest builds and uses that environment in each project and
leaves `.venv` to the host. Each machine gets its own.

## testvm and podman

In the guest:

```sh
testvm run -- 'apt-get update && apt-get install -y nginx'   # fresh VM, destroyed after
testvm up web -p 8080:80 && testvm ssh web                   # keep one around
testvm cp ./build web:/opt/ && testvm down web
podman run --rm docker.io/library/debian:trixie ...          # use full image names
```
