PYTHON ?= python3
GO ?= go
SCRIPT = src/automate-stream.py
INSPECT_SCRIPT = src/inspect-streams.py
PURGE_SCRIPT = src/purge-streams.py
GO_CMD = ./cmd/automate-stream
ATEM_SCRIPT = src/atem-streamkey
REQUIREMENTS := requirements.txt
VENV_DIR ?= .venv
VENV_PIP := $(VENV_DIR)/bin/pip
VENV_PYTHON := $(VENV_DIR)/bin/python
ATEM_IP ?= 192.168.1.87

.PHONY: help venv install auth run check

help:
        @printf "\nUsage:\n"
        @printf "  venv       Create local virtual environment\n"
        @printf "  install    Install Python dependencies\n"
        @printf "  auth       Run one-time OAuth bootstrap\n"
        @printf "  get        Get stream key from ATEM\n"
        @printf "  run        Create broadcast and update ATEM\n"
        @printf "  check      Verify required local files\n"
        @printf "  inspect    Inspect current and pending broadcasts\n"
        @printf "  purge      Purge old stream keys\n"
        @printf "  cleanup    Remove local virtual environment\n"
        @printf "  scp        Copy project files to remote server\n"
        @printf "  go-run     Create broadcast and update ATEM with the Go port\n"
        @printf "  go-cleanup Purge old, unbound RTMP stream keys with the Go port\n"
        @printf "  go-build   Compile the Go port\n"
        @printf "  go-auth    Run one-time OAuth bootstrap with the Go port\n"

scp:
        scp -r * fbcr2:apps/streamcreator2026/

venv:
        @test -d $(VENV_DIR) || $(PYTHON) -m venv $(VENV_DIR)

install: venv
        $(VENV_PIP) install -r $(REQUIREMENTS)

auth: venv
        $(VENV_PYTHON) $(SCRIPT) --auth-only

get:
        ATEM_IP=$(ATEM_IP) $(ATEM_SCRIPT)

run: check venv
        $(VENV_PYTHON) $(SCRIPT)

cleanup:
        $(VENV_PYTHON) $(SCRIPT) --cleanup

go-auth:
        $(GO) run $(GO_CMD) --auth-only

go-run: check
        ATEM_IP=$(ATEM_IP) $(GO) run $(GO_CMD)

go-cleanup:
        $(GO) run $(GO_CMD) --cleanup

go-build:
        $(GO) build $(GO_CMD)

check:
        @test -f client_secrets.json || (echo "Missing client_secrets.json in repo root" >&2; exit 1)
        @test -f config/broadcast.json || (echo "Missing config/broadcast.json" >&2; exit 1)

inspect:
        $(VENV_PYTHON) $(INSPECT_SCRIPT)

purge:
        $(VENV_PYTHON) $(PURGE_SCRIPT)
