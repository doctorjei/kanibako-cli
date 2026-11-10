# One-shot PG credentials

`SecretPayload` holds authorized, repr-hidden bytes. The codec validates names,
duplicates, lengths, and EOF before returning values; diagnostics contain no material.

`receive_environment` validates UTF-8/NUL-free values, acknowledges receipt, and closes
both inherited pipes on success or failure. `main` exports into its own environment
and immediately execs the existing entrypoint. No listener, token file, resident
wrapper, or command-lifetime supervision is created. General harness delivery is unchanged.

Host callers choose final PG winners and own transport deadlines. Native/empty launches
skip the receiver. Process memory is the destination; the pipe ends before agent startup.
