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
- The tools are installed in the project, not in the machine: setup and
  sessions set `MISE_DATA_DIR` to `.mise-` followed by the machine's
  name (`.mise-kelder` in the default machine) in the project's
  directory, so projects sharing a machine share no tools. It holds a
  `.gitignore` of its own, so git does not list it.
- Rust's toolchains and Cargo's files, which rustup would keep in the
  home directory, go there too (`MISE_RUSTUP_HOME` and
  `MISE_CARGO_HOME` are `.mise-kelder/rustup` and `.mise-kelder/cargo`).
- mise's cache, where it keeps what it downloads, is `/tmp/mise-cache`
  (`MISE_CACHE_DIR`). `/tmp` is in the machine's memory and is emptied
  when the machine stops, so no downloads stay on its disk either.
- Sessions have the project's shims (`.mise-kelder/shims`) first on
  `PATH`. A shim picks the version from the directory it is run in.
  A session uses the data directory of the directory it was started
  in, so a tool only an extra directory's config lists is not found.
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
