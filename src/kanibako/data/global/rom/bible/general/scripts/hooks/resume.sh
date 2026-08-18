#!/usr/bin/env bash

~/canon/bible/general/scripts/util/pid-add.sh "$PPID" >/dev/null 2>&1 || true
~/canon/handbook/general/scripts/hooks/resume.sh >/dev/null 2>&1 || true
