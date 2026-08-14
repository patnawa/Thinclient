# ThinClient PXE on a Debian Docker host

This deployment keeps OPNsense as the DHCP server and moves both file-serving
jobs to an always-on Debian host:

```text
PXE client --DHCP--> OPNsense legacy ISC DHCP
           --TFTP-> Debian:69   (boot loader, kernel, initrd)
           --HTTP-> Debian:8080 (kernel, initrd, squashfs, config.json)
```

TFTP deliberately uses Docker host networking. PXE firmware starts on UDP 69,
then TFTP transfers move to a dynamically selected UDP port; host networking
avoids fragile port-range and connection-tracking workarounds.

## Prerequisites

- A Debian host with a static address or DHCP reservation.
- Docker Engine with the Compose plugin.
- A generated single-profile `out/pxe` tree or merged `out/pxe-dual` tree.
- At least 1 GiB free on the Debian host for the PXE tree and container image.

Do not point this deployment at the ISO itself. Copy the generated PXE tree,
which already contains the BIOS, UEFI, Secure Boot, kernel, initrd, and squashfs
artifacts expected by the boot menus.

To offer separate fast and compatibility choices, build and merge both
profiles first:

```bash
sudo bash build/build-full-pxe.sh
sudo bash build/build-lite-pxe.sh
sudo bash build/merge-pxe-profiles.sh
```

The merged menu defaults to **Lite Auto Cache**, offers **Lite Network Only**
for a slow/suspect USB cache, and keeps **Full Drivers** for Wi-Fi or hardware
that Lite cannot start. A removable FAT32, exFAT, or ext4 partition labelled
`TCCACHE` is populated after the first network boot and avoids later root-image
downloads; no HDD is required.

For example, copy the current build from Windows:

```powershell
scp -r C:\Users\Alpha\Documents\GitHub\ThinClient\out\pxe user@192.168.1.20:/srv/thinclient/
```

Clone or copy this repository to the Debian host so Docker can build the small
server image. Ensure the copied artifacts are readable:

```bash
sudo chmod -R a+rX /srv/thinclient/pxe
```

## Build or pull the container

The server container contains TFTP and HTTP software only. The large PXE tree
is kept outside the image and mounted read-only, so rebuilding the container
does not duplicate the Lite and Full root files.

To build the image directly from a repository checkout:

```bash
cd Thinclient
sudo docker build \
  --file deploy/docker-pxe/Dockerfile \
  --tag thinclient-pxe-server:1.3 .
```

The normal `deploy.sh` command performs this local build automatically. A
published image is also available from GitHub Container Registry:

```bash
sudo docker pull ghcr.io/patnawa/thinclient-pxe-server:1.3
```

Publishing is automated by `.github/workflows/publish-container.yml` whenever
a GitHub release is published. It also supports a manual workflow run and
publishes both the numbered version and `latest` tags.

## Deploy

From the repository root on Debian, supply the Debian host's LAN address:

```bash
sudo ./deploy/docker-pxe/deploy.sh 192.168.1.20 8080 /srv/thinclient/pxe-dual
```

To deploy the published image instead of compiling it on the Debian host:

```bash
sudo env PXE_IMAGE=ghcr.io/patnawa/thinclient-pxe-server:1.3 \
  bash deploy/docker-pxe/deploy.sh \
  192.168.1.20 8080 /srv/thinclient/pxe-dual
```

The helper performs four operations:

1. Rewrites the generated boot menus to fetch files from
   `http://192.168.1.20:8080`.
2. Selects the UEFI TFTP kernel/initrd entry first. This avoids warm-reboot TCP
   timeouts seen with some NIC firmware; each large squashfs still uses HTTP.
3. Records the absolute PXE path, listen address, and HTTP port in an ignored
   `.env` file.
4. Builds and starts read-only TFTP and HTTP containers with automatic restart.

If `ufw` is enabled, permit only the PXE client subnet:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 69 proto udp
sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp
```

Do not expose either service on the WAN.

## OPNsense legacy ISC DHCP

Open **Services > ISC DHCPv4 > LAN > Network Booting**, enable network booting,
and set:

| Field | Value |
|---|---|
| Next-server | `192.168.1.20` (the Debian host, not OPNsense) |
| Default BIOS filename | `pxelinux.0` |
| UEFI 32-bit filename | leave blank |
| UEFI 64-bit filename | `bootx64.efi` |

`bootx64.efi` is the signed shim path and works with the ThinClient Secure Boot
chain. With Secure Boot disabled, `grub/x86_64-efi/core.efi` is also available.
Save and apply the DHCP configuration. The separate **TFTP Server** option can
remain blank; PXE uses the Network Booting `next-server` value.

For each additional client VLAN, apply the same network-boot values to that
interface's DHCP scope and allow that VLAN to reach the Debian host on UDP 69
and TCP 8080.

## Verify and operate

On Debian:

```bash
cd deploy/docker-pxe
sudo docker compose ps
sudo docker compose logs --follow
curl -I http://127.0.0.1:8080/thinclient/lite/filesystem.squashfs
curl -I http://127.0.0.1:8080/thinclient/full/filesystem.squashfs
```

From another LAN machine with a TFTP client:

```bash
tftp 192.168.1.20 -c get pxelinux.0
tftp 192.168.1.20 -c get bootx64.efi
curl -I http://192.168.1.20:8080/thinclient/lite/filesystem.squashfs
```

For a lab or office that may start many machines together, install `curl` and
`tftp-hpa` on a test machine and exercise both services with 50 parallel kernel
downloads:

```bash
bash deploy/docker-pxe/load-test.sh 192.168.1.20 50 8080
```

This proves request concurrency, not aggregate switch capacity. Fifty Lite
root downloads total about 18.1 GB, so a single 1 GbE uplink still imposes a
best-case transfer floor of roughly 2.4 minutes during a simultaneous boot.

After replacing the PXE tree with a new release, rerun `deploy.sh`. It retargets
the new boot menus and recreates containers only when their configuration or
image changed.

For rollback, point the same command at the retained previous PXE directory
and, when using GHCR, its previous image tag:

```bash
sudo env PXE_IMAGE=ghcr.io/patnawa/thinclient-pxe-server:1.3 \
  bash deploy/docker-pxe/deploy.sh \
  192.168.1.20 8080 /srv/thinclient/pxe-dual-previous
```

To stop the service without deleting the PXE artifacts:

```bash
cd deploy/docker-pxe
sudo docker compose down
```

## Troubleshooting

- **PXE-E32/TFTP timeout:** confirm UDP 69 is allowed and no other TFTP daemon
  already owns that port (`sudo ss -lunp | grep ':69'`).
- **GRUB loads but the OS does not:** inspect `docker compose logs http`, then
  fetch `filesystem.squashfs` from another LAN system.
- **UEFI access denied:** use `bootx64.efi` for Secure Boot clients.
- **Containers report missing artifacts:** the bind mount is wrong or contains
  the ISO instead of the generated `out/pxe` tree.
- **Clients on another VLAN fail:** check that the DHCP scope uses the Debian
  host as `next-server` and that OPNsense permits UDP 69 and TCP 8080 to it.
