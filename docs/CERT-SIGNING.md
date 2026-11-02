# Certificate signing key

From Modal round r04d (2 Nov 2026) on, live receipts and certificates are signed with the
WattNot certificate key, an Ed25519 key that lives only on the build box at
`~/.wattnot/cert-signing-key.pem` (mode 600, override with `$WATTNOT_CERT_KEY`). Its public
half is in `verifier/trusted-keys.json` as kind `ed25519-wattnot`, signer
`offline:a10d641cddc104d8` (`wattnot-cert-signing-2026-11-02`). If the key is missing, a
live round refuses to sign (fail closed) and preflight's `cert_key` check fails.

Everything signed before r04d, including certificate **WNC-0001**, was signed with the public
sample key in `verifier/samples/test-key.pem` (or a per-run throwaway key). Anyone can sign
with that key, so those signatures only show a file was not changed; they say nothing about
who ran the round. The verifier labels them `test-signed (public sample key)`. The old data is
left as it was.

The test key stays for tests, samples and `--fake` runs only. Rekor publishing is separate and
still needs Akash's okay.
