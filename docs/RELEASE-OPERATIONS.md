# Release operations

ThinClient 1.5 separates source checks, image checks, and deployment. A successful
build is not a successful boot test. Never replace a live PXE tree in place.

## Build and verify

Run source tests and static checks on Linux:

```bash
bash build/check.sh
bash build/lint.sh
bash build/unittest.sh
```

Keep signing keys outside the checkout in a root-only directory. Create separate
RSA keys for releases and central configuration with `openssl genpkey`. Pass
`RELEASE_SIGNING_KEY` and `CONFIG_SIGNING_KEY` to both profile builds. The build
copies only their public keys into the image and publishes detached signatures.
`DEFAULT_CONFIG_FILE` accepts a complete existing site configuration, preserving
explicit compatibility choices. `TC_CONFIG_LOCAL=/dev/null` disables local build
overrides when reproducing a generic build.

```bash
RELEASE_SIGNING_KEY=/secure/release.pem CONFIG_SIGNING_KEY=/secure/config.pem \
  OUTDIR="$PWD/out/release-1.5.1/lite" bash build/build-lite-pxe.sh
RELEASE_SIGNING_KEY=/secure/release.pem CONFIG_SIGNING_KEY=/secure/config.pem \
  OUTDIR="$PWD/out/release-1.5.1/full" bash build/build-full-pxe.sh
TC_TEST_CONFIG_SIGNING_KEY=/secure/config.pem bash build/verify-release.sh
```

The release gate extracts the final squashfs into a disposable Linux directory.
Runtime tests use those extracted files, never a source overlay copied over an
older artifact. Tests requiring generated menus use a separate PXE tree. Source
tests and host-side test tools remain identifiable as such.
The extraction filesystem must allow device nodes and setuid executables.
On WSL, `/tmp` is often `nodev,nosuid`; the gate uses `/var/tmp` instead, with
`TC_TEST_TMPDIR` available for another suitable Linux filesystem.

Both profiles must pass unit, permission, RDP, GTK, BIOS, UEFI, Secure Boot,
installer, PXE, and two-boot cache checks. QEMU boot tests require a readiness
message from the mapped connection-manager window. A bright screenshot or a
successfully launched QEMU process is insufficient. The virtio test channel
exists only in the VM tests; physical machines never write a physical serial
port or upload readiness information.

Successful checks create `verification.json`, binding the result to the exact
kernel, initrd and squashfs hashes. The deployment helper requires a matching
receipt for new trees carrying a release manifest. Do not create a receipt by
hand. Rebuilds invalidate earlier receipts.

```bash
bash build/merge-pxe-profiles.sh \
  out/release-1.5.1/full/pxe out/release-1.5.1/lite/pxe out/pxe-dual-1.5.1
python3 tools/release-manifest.py verify out/pxe-dual-1.5.1/thinclient/lite \
  --key /secure/release.pub
bash build/package-deployment.sh out/pxe-dual-1.5.1 /secure/release.pub \
  out/deployment-1.5.1.tar
```

The packager checks both receipts and signatures before creating a new archive.
Keep private keys outside the checkout; standard private-key files, local build
overrides, credentials directories and session captures are excluded. The Docker
build context is separately allowlisted to just the server's required sources.

## Trust and administrator policy

Public GitHub ISO releases must use fresh, separate build directories with
`TC_CONFIG_LOCAL=/dev/null`. Leave `DEFAULT_CONFIG_FILE`, `TRUST_POLICY_FILE`,
`SUPPORT_AUTHORIZED_KEYS_FILE`, and all configuration/release key inputs unset.
Do not reuse a site's provisioned root filesystem: it can retain trust keys and
other local files even when a later build omits those inputs. Run
`RELEASE_OUT=/path/to/public-output bash build/verify-release.sh` against both
public profiles before uploading only the ISO files and their SHA-256 sidecars.
These generic images intentionally do not enforce a site's configuration
signature policy; build a site-specific image to establish that trust policy.
Never upload a site deployment archive, local handoff, or test capture as a
public release asset.

New factory connections use `cert_policy: strict`; Terminal defaults to disabled.
An empty administrator password opens explicit setup instead of granting access
to support tools. Set a persistent administrator hash centrally for diskless
clients; a session-only password otherwise disappears on reboot. New hashes use
PBKDF2-SHA256, while existing SHA-256 and plaintext configurations remain readable
for migration. Validate production configuration before publishing it:

```bash
python3 tools/check-config.py config.json --production
openssl dgst -sha256 -sign /secure/config.pem -out config.json.sig config.json
```

Sign each per-device file as well. GET and HEAD requests for `config.json.sig`
select the matching per-MAC signature. MAC selection is not authentication.
Configuration changes should be published as an immutable directory or with a
coordinated atomic switch; independently replacing JSON and its signature can
temporarily cause rejection, retaining the last valid runtime configuration.

`TRUST_POLICY_FILE` installs immutable `/etc/thinclient/policy.json` settings:

```json
{
  "production": true,
  "config_required": true,
  "require_https": true,
  "config_url": "https://config.example.com:8443/config.json",
  "config_public_key": "/etc/thinclient/config.pub"
}
```

Install the internal CA on TCCONF under `ca-certificates/*.crt` or in the image's
trusted certificate store. The optional Compose `https` profile provides a
separate TLS listener; mount `server.crt` and `server.key` using `TLS_CERT_DIR`,
with the key readable only by its service account. Existing HTTP/TFTP services
remain available for firmware and old clients. Do not change certificate policy
on a live fleet until its RDP and configuration certificates are trusted.

The configuration fetch has a 20-second overall network budget. The service
separately bounds fetching and applying device settings, so a fetch timeout does
not skip all device configuration. A failed refresh preserves the last validated
runtime copy. A fresh boot with `config_required` stays in the support UI until
approved central configuration arrives. Runtime fallback is not a persistent
offline configuration cache. Help reports source, state, version and last success.

Release signatures authenticate the manifest and root against the public key in
the initramfs, including cache hits. **The initramfs itself must be trusted.**
Stock shim/kernel signatures alone do not authenticate an arbitrary replacement
initrd. Protect that earlier boot chain with site-enrolled signed boot artifacts
or a GRUB configuration enforcing detached signatures. See the
[GNU GRUB signature documentation](https://www.gnu.org/software/grub/manual/grub/html_node/Using-digital-signatures.html).
Do not claim a complete authenticated PXE chain until that trust anchor has been
enrolled and tested on the intended firmware. Key rotation requires a new image;
retain the preceding signed release for rollback.

## Fleet measurements and canaries

The existing `/status` dashboard and persistent history remain available.
`/metrics` exports aggregate Prometheus request, failure, byte, boot-request and
active-transfer counts, without MAC/IP labels. These are file-service counts,
not proof of successful client authentication.

Clients keep at most 128 local lifecycle events in `/run/thinclient/events.jsonl`.
Events include UI readiness, client process startup and session outcomes, with
RAM/uptime measurements. They omit connection names, endpoints and credentials.
Nothing is automatically uploaded. Collect explicitly when needed:

```bash
tc-diag --json
python3 tools/fleet-report.py collected/*.jsonl
```

Compare UI-ready uptime and RAM by version, profile and cache state. Client process
startup is not remote-login completion. Record physical model, NIC/GPU driver,
firmware, Secure Boot state, RAM, and observed result alongside inventories.
Virtual boot success does not certify a physical model. Set `TC_TEST_RAM_MB=1024`
on the PXE test to investigate low-memory behavior; do not advertise that RAM
size without a successful test.

Prepare a new combined tree for selected MACs without modifying either input:

```bash
python3 tools/prepare-canary.py stable-tree candidate-tree new-canary-tree \
  --mac aa:bb:cc:dd:ee:ff
```

BIOS uses per-MAC PXELINUX configuration; UEFI selects the candidate via GRUB's
default network-interface MAC. The stable menu remains the default for all other
clients. Render both input trees for the final server address before preparing a
canary; do not subsequently apply the ordinary deployment menu renderer to the
combined canary tree. No MAC is selected automatically.

## Live-server rollout and rollback

1. Inspect the live Compose working directory, image IDs/digests, bind mounts,
   status volume, listening addresses and health. Preserve server-side changes.
2. Back up Compose files, site configuration/signatures, public trust metadata,
   monitor state and the current image identity. Protect any files with secrets.
3. Upload to a **new** versioned directory only after local boot gates pass.
   Verify hashes again after transfer. Keep both the current and previous trees.
4. Build and test the candidate server on an unused port and a separate status
   volume. Verify configuration selection, metrics, TLS when enabled, and HTTP
   artifact hashes. Keep the live containers running during these checks.
5. Switch only the ThinClient services. Retain the production monitor volume;
   never use `docker compose down --volumes`. Check health, TFTP, HTTP, counters,
   read-only mounts and exact image identity immediately after the switch.
6. On failure, restore the recorded Compose environment and prior image/tree.
   Do not delete a previous release to make room for a failed replacement.
7. Record physical boot evidence before broad fleet adoption. Firmware enrollment
   and testing physical machines require access to those machines.
