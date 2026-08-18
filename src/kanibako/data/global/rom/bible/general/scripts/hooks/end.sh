#!/usr/bin/env bash

~/canon/bible/general/scripts/util/pid-rm.sh "$PPID" >/dev/null 2>&1 || true
~/canon/handbook/general/scripts/hooks/end.sh >/dev/null 2>&1 || true
