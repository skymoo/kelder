# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
# make install                      installs to ~/.local (bin/ must be on PATH)
# make install PREFIX=/usr/local    system-wide (run as root)
# make uninstall [PREFIX=...]       removes what install put there
# make check                        lint (needs shellcheck)
PREFIX  ?= $(HOME)/.local
BINDIR  ?= $(PREFIX)/bin
DATADIR ?= $(PREFIX)/share/smol-claude

BIN   = bin/smol-claude
LIB   = lib/smol.sh lib/setup.sh
GUEST = guest/testvm guest/containers-tsi.conf

.PHONY: all install uninstall check

all:
	@echo "Nothing to build. Run 'make install' (PREFIX=$(PREFIX))."

install:
	install -d $(DESTDIR)$(BINDIR) $(DESTDIR)$(DATADIR)/lib $(DESTDIR)$(DATADIR)/guest
	install -m 755 $(BIN) $(DESTDIR)$(BINDIR)/
	install -m 644 $(LIB) $(DESTDIR)$(DATADIR)/lib/
	install -m 755 guest/testvm $(DESTDIR)$(DATADIR)/guest/
	install -m 644 guest/containers-tsi.conf $(DESTDIR)$(DATADIR)/guest/

uninstall:
	rm -f $(addprefix $(DESTDIR)$(BINDIR)/,$(notdir $(BIN)))
	rm -rf $(DESTDIR)$(DATADIR)

check:
	shellcheck -x $(BIN) lib/setup.sh   # follows them into lib/smol.sh
	python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' guest/testvm
