# ThinClient project handoff and improvement roadmap

This document preserves the reasoning and repeatable checks behind the current
design. Keep site addresses, passwords, customer names, and deployment-specific
paths in an ignored `docs/*.local.md` file instead of this public document.

## Current release architecture

ThinClient 1.3 provides two independently versioned x86-64 images:

- **Lite** is the default for common Dell, HP, Acer, ASUS, ASRock, and Gigabyte
  office hardware. It retains wired networking, graphics, audio, RDP/VNC,
  legacy BIOS, UEFI, Secure Boot, and the installer while omitting Wi-Fi and
  less-common firmware.
- **Full Drivers** is the compatibility fallback for Wi-Fi, uncommon network
  adapters, newer audio firmware, and hardware that Lite cannot initialize.

The merged PXE tree supports legacy BIOS and UEFI. OPNsense or another existing
DHCP server remains authoritative; the Debian Docker host provides only TFTP
and HTTP. Firmware downloads its boot loader, kernel, and initrd through TFTP.
Linux then transfers the much larger squashfs through HTTP.

The default **Lite Auto Cache** option can save a checksum-addressed squashfs
to a removable USB partition labelled `TCCACHE`. A cache hit is verified while
the file is copied into RAM, after which the USB is unmounted. A miss uses the
normal HTTP path and saves the verified root atomically after startup. The
**Lite Network Only** entry is the recovery path for a missing or slow USB.

The PXE server container is intentionally small. It contains TFTP and HTTP
software, while the generated PXE tree is mounted read-only from the Debian
host. Releases publish both the ISOs and a reusable GHCR server image.

## Design guardrails

Preserve these properties when changing the project:

1. OPNsense stays the only DHCP authority. Never add a second DHCP server to
   the Docker deployment.
2. Do not serve the ISO directly for PXE. Serve the generated PXE tree.
3. TFTP is for firmware compatibility; HTTP carries large artifacts.
4. The cache accepts only a removable USB device with the configured label. It
   must never select or format an internal disk automatically.
5. Cache filenames are content hashes, and every hit is verified before use.
6. Lite and Full use separate cache namespaces so profiles cannot collide.
7. Configuration remains centrally fetched on every boot even when the OS root
   comes from USB. A cached root must not freeze old site settings.
8. Build output, real credentials, session captures, and site-specific network
   data remain outside Git.
9. Keep old immutable PXE trees until the replacement passes a physical boot;
   rollback should only require rerunning `deploy.sh` with the old path/tag.
10. Do not claim full UEFI, Secure Boot, or hardware support without recording
    the exact machine or virtual firmware used for the test.

## Release gate

Run these checks in order for every release:

1. `git diff --check` and `python -m unittest discover -s tests`.
2. `bash build/check.sh` and the permission/security checks in the build
   container.
3. Build Lite and Full independently, record sizes and SHA-256 values, and
   merge them with `build/merge-pxe-profiles.sh`.
4. Run `build/cachecheck.sh` against both initramfs variants.
5. Run `build/cacheboottest.sh`: the first boot must fetch one squashfs and
   populate `TCCACHE`; the second must fetch zero squashfs files while still
   retrieving central configuration and reaching the graphical session.
6. Boot-test legacy BIOS and UEFI. Test Secure Boot whenever compatible virtual
   firmware or physical hardware is available.
7. Deploy an immutable PXE directory, verify both checksum sidecars, HTTP HEAD
   responses, TFTP transfer, container health, and the read-only bind mount.
8. Run the 50-client kernel concurrency test. Treat this as a service test, not
   proof that a 1 GbE switch can deliver 50 root images instantly.
9. Push source, create an annotated version tag, publish both ISOs and checksum
   files, and verify the server-side release digests.
10. Publish the GHCR image, test it with an empty Docker credential directory,
    and deploy that exact digest before declaring the release complete.

## Measurement baseline

Record these values for every release so regressions are visible:

- Lite and Full ISO, squashfs, kernel, and initrd sizes.
- Cold first-boot time, warm USB-cache boot time, and time to graphical session.
- Server bytes transferred and peak throughput for 1, 10, and 50 clients.
- Client RAM usage after the squashfs is copied into RAM.
- Cache write duration and USB health/failure rate by device model.
- Boot success by firmware mode, Secure Boot state, NIC, GPU, and machine model.

## Prioritized improvements

### P0: fleet reliability and security

- Sign a small release manifest containing the Lite/Full hashes and verify the
  signature in the initramfs. A raw SHA supplied on the kernel command line
  detects corruption but does not authenticate a hostile PXE server.
- Put central configuration behind HTTPS with a trusted internal CA. Keep
  passwords out of JSON where possible and prefer user entry or Kerberos.
- Add structured server metrics: boot start, selected profile, cache hit/miss,
  bytes served, time to session, and failure reason. Avoid logging secrets.
- Back up the central configuration, per-device overrides, Docker deployment
  files, and the current/previous PXE tree metadata. Test restoration.
- Validate at least one physical legacy BIOS client, one UEFI Secure Boot
  client, and one Full-only hardware client before broad rollout.

### P1: performance and operations

- Create a controlled USB pre-warming tool that writes a release by hash and
  verifies it. This avoids 50 simultaneous first-boot squashfs downloads.
- Add boot staggering or a concurrency-aware HTTP admission policy for cold
  fleets on 1 GbE. A faster server disk cannot overcome a saturated uplink.
- Capture cache status in the diagnostics UI, including device, filesystem,
  expected hash, hit/miss reason, write completion, and measured read speed.
- Add a cache-space policy and USB wear/failure reporting. Never delete files
  outside the dedicated `thinclient-cache/<profile>` namespace.
- Produce an automated hardware inventory export from `tc-diag` to drive Lite
  driver decisions with evidence instead of adding firmware speculatively.
- Add a low-RAM test lane. Copying the root to RAM is fast and resilient but may
  be inappropriate for 1 GB clients; document or implement an NFS alternative.

### P2: maintainability

- Add CI jobs for shell syntax, ShellCheck, Python tests, Docker build, and PXE
  tree structural checks on every pull request.
- Add scheduled dependency rebuilds and vulnerability scans for the Debian
  client and GHCR image, with reproducible base-image digest tracking.
- Publish multi-architecture server containers if an ARM Debian PXE host is
  needed. The client images remain x86-64.
- Automate release notes from measured artifact sizes, hashes, boot tests, and
  the improvement log.
- Add a small canary deployment mode so a selected MAC receives a new profile
  before the default fleet menu is changed.

## Questions to ask before the next change

- Does this change increase initrd size or move work onto the firmware path?
- Does it affect cold boots, warm cache boots, or both?
- What happens when the network disappears halfway through a download?
- What happens when the USB is slow, corrupt, duplicated, removed, or full?
- Can 50 clients trigger a race, thundering herd, port exhaustion, or memory
  spike that a single-client test cannot show?
- Can an untrusted LAN host replace the boot artifact or configuration?
- Is rollback still possible without rebuilding anything?
- Which physical hardware proves the support claim?

## Starting the next work session

Read this file, `deploy/docker-pxe/README.md`, and the ignored site handoff if
present. Then check the working tree, current release/tag, live container image
digest, PXE bind-mount path, checksum sidecars, and container health before
changing any source or production state.
