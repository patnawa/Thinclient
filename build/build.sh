#!/bin/bash
# =============================================================================
#  ThinClient image builder
#  Produces a hybrid BIOS+UEFI ISO and a matching set of PXE/netboot artifacts.
#  Run as root on a Debian/Ubuntu host (WSL2 works):  sudo bash build/build.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Scripts may live on a Windows filesystem; strip CRLF before sourcing anything.
sed -i 's/\r$//' "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.list 2>/dev/null || true
# shellcheck source=config.sh
source "$SCRIPT_DIR/config.sh"

OUTDIR="${OUTDIR:-$REPO_DIR/out}"
ROOTFS="$WORKDIR/rootfs"
IMAGE="$WORKDIR/image"
STAMP_START=$(date +%s)

log()  { printf '\033[1;36m[%(%H:%M:%S)T]\033[0m \033[1m%s\033[0m\n' -1 "$*"; }
warn() { printf '\033[1;33m[%(%H:%M:%S)T] WARN\033[0m %s\n' -1 "$*"; }
die()  { printf '\033[1;31m[%(%H:%M:%S)T] ERROR\033[0m %s\n' -1 "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (sudo bash build/build.sh)"

for t in debootstrap mksquashfs xorriso grub-mkstandalone mkfs.vfat mcopy rsync; do
  command -v "$t" >/dev/null || die "missing build tool: $t  (see README prerequisites)"
done

case "$WORKDIR" in
  /mnt/*) die "WORKDIR must be on the Linux filesystem, not $WORKDIR" ;;
esac

# ----------------------------------------------------------------- cleanup ---
unmount_all() {
  local m
  for m in dev/pts dev/shm dev proc sys run; do
    mountpoint -q "$ROOTFS/$m" && umount -lf "$ROOTFS/$m" 2>/dev/null || true
  done
}
trap unmount_all EXIT

# ================================================================ stage 1 ====
# Bootstrap a minimal Debian base system.
# =============================================================================
if [ -f "$ROOTFS/.bootstrap-ok" ] && [ "${REBUILD_BASE:-0}" != "1" ]; then
  log "stage 1  reusing existing bootstrap (REBUILD_BASE=1 to force)"
else
  log "stage 1  bootstrapping Debian $SUITE/$ARCH"
  unmount_all
  rm -rf "$ROOTFS"
  mkdir -p "$ROOTFS"
  debootstrap --arch="$ARCH" --variant=minbase --components="$COMPONENTS" \
              --include=apt-utils,ca-certificates \
              "$SUITE" "$ROOTFS" "$MIRROR" \
    || die "debootstrap failed"
  touch "$ROOTFS/.bootstrap-ok"
fi

# ================================================================ stage 2 ====
# Provision inside the chroot, then lay the overlay down on top.
# Order matters: overlay last, so our files beat any package conffile.
# =============================================================================
log "stage 2  provisioning chroot"
install -m 0755 "$SCRIPT_DIR/chroot-setup.sh"    "$ROOTFS/tmp/chroot-setup.sh"
install -m 0755 "$SCRIPT_DIR/chroot-finalize.sh" "$ROOTFS/tmp/chroot-finalize.sh"
install -m 0644 "$SCRIPT_DIR/packages.list"      "$ROOTFS/tmp/packages.list"

mkdir -p "$ROOTFS"/{dev,proc,sys,run}
mount -t proc  proc  "$ROOTFS/proc"
mount -t sysfs sys   "$ROOTFS/sys"
mount --bind /dev    "$ROOTFS/dev"
mount -t devpts devpts "$ROOTFS/dev/pts" 2>/dev/null || true

in_chroot() {
  chroot "$ROOTFS" /usr/bin/env -i \
    HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin TERM=xterm \
    DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C \
    SUITE="$SUITE" MIRROR="$MIRROR" SECURITY_MIRROR="$SECURITY_MIRROR" \
    COMPONENTS="$COMPONENTS" DISTRO_NAME="$DISTRO_NAME" \
    DISTRO_VERSION="$DISTRO_VERSION" \
    INCLUDE_PRINTING="$INCLUDE_PRINTING" INCLUDE_SMARTCARD="$INCLUDE_SMARTCARD" \
    INCLUDE_VNC="$INCLUDE_VNC" \
    INCLUDE_USB_REDIR="$INCLUDE_USB_REDIR" INCLUDE_WIFI="$INCLUDE_WIFI" \
    INCLUDE_WIFI_FIRMWARE="$INCLUDE_WIFI_FIRMWARE" \
    INCLUDE_SOF_FIRMWARE="$INCLUDE_SOF_FIRMWARE" \
    INCLUDE_AMD_MICROCODE="$INCLUDE_AMD_MICROCODE" \
    INCLUDE_ADMIN_TOOLS="$INCLUDE_ADMIN_TOOLS" \
    INCLUDE_SSH_SERVER="$INCLUDE_SSH_SERVER" \
    INITRAMFS_MODULES="$INITRAMFS_MODULES" \
    INITRAMFS_NET_MODULES="$INITRAMFS_NET_MODULES" \
    ENABLE_USB_CACHE="$ENABLE_USB_CACHE" CACHE_LABEL="$CACHE_LABEL" \
    CACHE_PROFILE="$CACHE_PROFILE" \
    DEFAULT_TIMEZONE="$DEFAULT_TIMEZONE" DEFAULT_KEYMAP="$DEFAULT_KEYMAP" \
    DEFAULT_NTP="$DEFAULT_NTP" \
    /bin/bash "$1"
}

in_chroot /tmp/chroot-setup.sh || die "chroot provisioning failed"

log "stage 2  applying overlay"

# Copy overlay, normalising line endings on the way in. Repository files come
# from DrvFS on the supported Windows/WSL build path, where every directory and
# file can appear as mode 0777. Never preserve those untrusted host modes.
rsync -rlt --safe-links --exclude '.git' --exclude '__pycache__' \
  "$REPO_DIR/overlay/" "$ROOTFS/"
# The repository changelog is canonical; ship the same file in the appliance
# so Help -> What's new remains available on a completely offline client.
install -Dm0644 "$REPO_DIR/CHANGELOG.md" \
  "$ROOTFS/usr/share/thinclient/CHANGELOG.md"
while IFS= read -r -d '' f; do
  case "$(file -b --mime-type "$f")" in
    text/*|application/json|application/xml|inode/x-empty) sed -i 's/\r$//' "$f" ;;
  esac
done < <(find "$ROOTFS/etc/thinclient" "$ROOTFS/usr/local" "$ROOTFS/etc/systemd" \
              "$ROOTFS/etc/udev" "$ROOTFS/etc/X11" "$ROOTFS/etc/NetworkManager" \
              "$ROOTFS/etc/initramfs-tools" "$ROOTFS/usr/lib/live" \
              "$ROOTFS/usr/share/thinclient" \
              "$ROOTFS/etc/sudoers.d" "$ROOTFS/etc/openbox" "$ROOTFS/etc/ssh" \
              -type f -print0 2>/dev/null)

# Normalize every overlay destination directory plus the trusted ancestors
# used to reach privileged helpers. A 0755 helper inside a writable parent is
# still replaceable and therefore equivalent to a passwordless root shell.
chmod 0755 "$ROOTFS" "$ROOTFS/etc" "$ROOTFS/usr" "$ROOTFS/usr/local"
while IFS= read -r -d '' d; do
  relative="${d#$REPO_DIR/overlay}"
  [ -z "$relative" ] || {
    chown root:root "$ROOTFS$relative"
    chmod 0755 "$ROOTFS$relative"
  }
done < <(find "$REPO_DIR/overlay" -type d ! -name __pycache__ -print0)
while IFS= read -r -d '' f; do
  relative="${f#$REPO_DIR/overlay}"
  chown root:root "$ROOTFS$relative"
  chmod 0644 "$ROOTFS$relative"
done < <(find "$REPO_DIR/overlay" -type f ! -path '*/__pycache__/*' -print0)

# Executable bits do not survive a Windows filesystem; set them explicitly.
chmod 0755 "$ROOTFS"/usr/local/bin/* "$ROOTFS"/usr/local/sbin/* 2>/dev/null || true
chmod 0755 "$ROOTFS"/etc/NetworkManager/dispatcher.d/* 2>/dev/null || true
chmod 0755 "$ROOTFS"/etc/initramfs-tools/hooks/* 2>/dev/null || true
chmod 0755 "$ROOTFS"/usr/lib/live/boot/9991-thinclient-cache.sh 2>/dev/null || true
# DrvFS commonly presents repository files and newly created directories as
# 0777. Normalize complete unit/drop-in trees: a writable systemd or sshd
# configuration would let the kiosk user defeat service security policy.
find "$ROOTFS/etc/systemd/system" -type d -exec chmod 0755 {} +
find "$ROOTFS/etc/systemd/system" -type f -exec chmod 0644 {} +
if [ -d "$ROOTFS/etc/ssh/sshd_config.d" ]; then
  find "$ROOTFS/etc/ssh/sshd_config.d" -type d -exec chmod 0755 {} +
  find "$ROOTFS/etc/ssh/sshd_config.d" -type f -exec chmod 0644 {} +
fi
chmod 0440 "$ROOTFS"/etc/sudoers.d/* 2>/dev/null || true
chmod 0755 "$ROOTFS"/usr/local/lib/thinclient/*.py 2>/dev/null || true

# Bake build-time defaults into the factory config.
python3 - "$ROOTFS/etc/thinclient/config.json" <<PYEOF
import json, sys
p = sys.argv[1]
c = json.load(open(p))
c["device"]["timezone"] = "${DEFAULT_TIMEZONE}"
c["device"]["keyboard_layout"] = "${DEFAULT_KEYMAP}"
c["device"]["ntp_server"] = "${DEFAULT_NTP}"
c["connections"][0]["host"] = "${DEFAULT_SERVER}"
c["connections"][0]["name"] = "${DEFAULT_SERVER_NAME}"
c["connections"][0]["domain"] = "${DEFAULT_DOMAIN}"
json.dump(c, open(p, "w"), indent=2)
PYEOF

# Export the *baked* configuration next to the ISO. The overlay copy still holds
# the placeholder addresses from the template, so anything that seeds a TCCONF
# partition has to read this file, not the one in overlay/. Getting that wrong
# puts a wrong server address on the stick, which overrides the correct one in
# the image and looks exactly like a network fault.
mkdir -p "$OUTDIR"
cp "$ROOTFS/etc/thinclient/config.json" "$OUTDIR/config.json"

# Prove the substitution actually happened. A client that boots with the
# template address reports a transport failure, which reads as a network fault
# and costs far more to diagnose than this check costs to run.
BAKED_HOST="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["connections"][0]["host"])' \
              "$OUTDIR/config.json")"
[ "$BAKED_HOST" = "$DEFAULT_SERVER" ] \
  || die "config bake failed: image says '$BAKED_HOST', DEFAULT_SERVER is '$DEFAULT_SERVER'"
log "stage 2  exported out/config.json (server: $BAKED_HOST)"

log "stage 2  finalising chroot"
in_chroot /tmp/chroot-finalize.sh || die "chroot finalisation failed"
rm -f "$ROOTFS"/tmp/chroot-*.sh "$ROOTFS"/tmp/packages.list

unmount_all

# ================================================================ stage 3 ====
# Squash the root filesystem.
# =============================================================================
log "stage 3  building squashfs (${SQUASH_COMP}, level ${SQUASH_LEVEL})"
rm -rf "$IMAGE"
mkdir -p "$IMAGE/live" "$IMAGE/isolinux" "$IMAGE/boot/grub" "$IMAGE/EFI/BOOT"

KVER="$(basename "$(ls -1 "$ROOTFS"/boot/vmlinuz-* | sort -V | tail -1)" | sed 's/^vmlinuz-//')"
[ -n "$KVER" ] || die "no kernel found in rootfs"
log "stage 3  kernel $KVER"
cp "$ROOTFS/boot/vmlinuz-$KVER"   "$IMAGE/live/vmlinuz"
cp "$ROOTFS/boot/initrd.img-$KVER" "$IMAGE/live/initrd.img"

mksquashfs "$ROOTFS" "$IMAGE/live/filesystem.squashfs" \
  -comp "$SQUASH_COMP" -Xcompression-level "$SQUASH_LEVEL" -b "$SQUASH_BLOCK" \
  -noappend -no-recovery -wildcards \
  -e 'boot/vmlinuz*' 'boot/initrd.img*' '.bootstrap-ok' \
     'tmp/*' 'var/cache/apt/archives/*.deb' 'var/lib/apt/lists/*' \
     'usr/share/doc/*' 'usr/share/man/*' 'usr/share/info/*' \
     'var/log/*' 'root/.bash_history' \
  || die "mksquashfs failed"

printf '%s' "$(du -sx --block-size=1 "$ROOTFS" | cut -f1)" > "$IMAGE/live/filesystem.size"
SQUASH_MB=$(( $(stat -c%s "$IMAGE/live/filesystem.squashfs") / 1024 / 1024 ))
SQUASH_SHA="$(sha256sum "$IMAGE/live/filesystem.squashfs" | awk '{print $1}')"
printf '%s  filesystem.squashfs\n' "$SQUASH_SHA" \
  > "$IMAGE/live/filesystem.squashfs.sha256"
CACHE_CMDLINE=""
case "$ENABLE_USB_CACHE" in 0|1) ;; *) die "ENABLE_USB_CACHE must be 0 or 1" ;; esac
printf '%s' "$CACHE_PROFILE" | grep -Eq '^[A-Za-z0-9._-]{1,32}$' \
  || die "CACHE_PROFILE must contain only letters, numbers, dot, underscore or dash"
printf '%s' "$CACHE_LABEL" | grep -Eq '^[A-Za-z0-9._-]{1,32}$' \
  || die "CACHE_LABEL must contain only letters, numbers, dot, underscore or dash"
if [ "$ENABLE_USB_CACHE" = 1 ]; then
  CACHE_CMDLINE="tc.cache=1 tc.cache.label=$CACHE_LABEL tc.cache.profile=$CACHE_PROFILE tc.cache.sha256=$SQUASH_SHA"
fi
log "stage 3  squashfs = ${SQUASH_MB} MB"

# ================================================================ stage 4 ====
# Bootloaders: isolinux for BIOS, GRUB (optionally shim-signed) for UEFI.
# =============================================================================
log "stage 4  assembling bootloaders"

SYSLINUX_DIR=/usr/lib/syslinux/modules/bios
ISOLINUX_BIN=/usr/lib/ISOLINUX/isolinux.bin
[ -f "$ISOLINUX_BIN" ] || die "isolinux not found - apt install isolinux syslinux-common"
cp "$ISOLINUX_BIN" "$IMAGE/isolinux/"
for m in ldlinux.c32 libcom32.c32 libutil.c32 vesamenu.c32 menu.c32; do
  [ -f "$SYSLINUX_DIR/$m" ] && cp "$SYSLINUX_DIR/$m" "$IMAGE/isolinux/"
done

cat > "$IMAGE/isolinux/isolinux.cfg" <<EOF
UI menu.c32
PROMPT 0
TIMEOUT 30
MENU TITLE $DISTRO_NAME $DISTRO_VERSION

LABEL live
  MENU LABEL ^Start $DISTRO_NAME
  MENU DEFAULT
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img $KERNEL_CMDLINE

LABEL toram
  MENU LABEL Start $DISTRO_NAME (^copy to RAM, unplug the USB after boot)
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img $KERNEL_CMDLINE toram

LABEL safe
  MENU LABEL Start in ^safe graphics mode
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img $KERNEL_CMDLINE nomodeset tc.safegfx=1

LABEL install
  MENU LABEL ^Install to this computer's internal disk
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img $KERNEL_CMDLINE tc.install=1

LABEL debug
  MENU LABEL Start with ^diagnostic console
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img boot=live components union=overlay tc.debug=1 console=tty0 console=ttyS0,115200
EOF

# --- GRUB config, shared by the ISO's UEFI path and by UEFI PXE -------------
mk_grub_cfg() {
  cat <<EOF
set default=0
set timeout=3
insmod all_video
insmod gfxterm
terminal_output gfxterm

menuentry "Start $DISTRO_NAME" {
    linux  /live/vmlinuz $KERNEL_CMDLINE
    initrd /live/initrd.img
}
menuentry "Start $DISTRO_NAME (copy to RAM)" {
    linux  /live/vmlinuz $KERNEL_CMDLINE toram
    initrd /live/initrd.img
}
menuentry "Start in safe graphics mode" {
    linux  /live/vmlinuz $KERNEL_CMDLINE nomodeset tc.safegfx=1
    initrd /live/initrd.img
}
menuentry "Install to this computer's internal disk" {
    linux  /live/vmlinuz $KERNEL_CMDLINE tc.install=1
    initrd /live/initrd.img
}
menuentry "Start with diagnostic console" {
    linux  /live/vmlinuz boot=live components union=overlay tc.debug=1 console=tty0 console=ttyS0,115200
    initrd /live/initrd.img
}
EOF
}
mk_grub_cfg > "$IMAGE/boot/grub/grub.cfg"

# Secure Boot: reuse Debian's signed shim + grub so the image boots on stock
# firmware without the customer disabling Secure Boot.
SECUREBOOT_OK=0
if [ "$INCLUDE_SECUREBOOT" = "1" ] \
   && [ -f "$ROOTFS/usr/lib/shim/shimx64.efi.signed" ] \
   && [ -f "$ROOTFS/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed" ]; then
  log "stage 4  Secure Boot: using Debian signed shim + grub"
  mkdir -p "$IMAGE/EFI/debian"
  cp "$ROOTFS/usr/lib/shim/shimx64.efi.signed"                      "$IMAGE/EFI/BOOT/BOOTX64.EFI"
  cp "$ROOTFS/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed"    "$IMAGE/EFI/BOOT/grubx64.efi"
  [ -f "$ROOTFS/usr/lib/shim/mmx64.efi.signed" ] && \
    cp "$ROOTFS/usr/lib/shim/mmx64.efi.signed"                      "$IMAGE/EFI/BOOT/mmx64.efi"
  # Signed grub only reads its config from this fixed prefix.
  printf 'search --no-floppy --set=root --file /live/vmlinuz\nset prefix=($root)/boot/grub\nconfigfile /boot/grub/grub.cfg\n' \
    > "$IMAGE/EFI/debian/grub.cfg"
  SECUREBOOT_OK=1
else
  [ "$INCLUDE_SECUREBOOT" = "1" ] && warn "signed shim/grub unavailable - image will need Secure Boot disabled"
  log "stage 4  building unsigned standalone GRUB EFI"
  cat > "$WORKDIR/grub-early.cfg" <<'EOF'
search --no-floppy --set=root --file /live/vmlinuz
set prefix=($root)/boot/grub
configfile /boot/grub/grub.cfg
EOF
  grub-mkstandalone -O x86_64-efi -o "$IMAGE/EFI/BOOT/BOOTX64.EFI" \
    --modules="part_gpt part_msdos fat iso9660 normal linux search search_fs_file all_video gfxterm configfile echo test" \
    "boot/grub/grub.cfg=$WORKDIR/grub-early.cfg" || die "grub-mkstandalone failed"
fi

# El Torito UEFI boot image (a small FAT filesystem holding /EFI).
log "stage 4  creating EFI boot image"
EFI_IMG="$IMAGE/boot/grub/efi.img"
EFI_KB=$(( ( $(du -sk "$IMAGE/EFI" | cut -f1) + 1024 ) ))
[ "$EFI_KB" -lt 2048 ] && EFI_KB=2048
rm -f "$EFI_IMG"
mkfs.vfat -C -n TCEFI "$EFI_IMG" "$EFI_KB" >/dev/null
mmd  -i "$EFI_IMG" ::/EFI ::/EFI/BOOT
mcopy -i "$EFI_IMG" -s "$IMAGE/EFI/BOOT"/* ::/EFI/BOOT/
if [ "$SECUREBOOT_OK" = "1" ]; then
  mmd   -i "$EFI_IMG" ::/EFI/debian
  mcopy -i "$EFI_IMG" "$IMAGE/EFI/debian/grub.cfg" ::/EFI/debian/
fi

# A raw-written USB should be useful without a second partitioning step. Keep
# persistence separate from ISO9660: the operating system remains immutable,
# while this small FAT32 filesystem can be mounted read-write by the client and
# edited from Windows. It is seeded from the already-baked configuration, never
# from the placeholder in overlay/.
TCCONF_IMG="$WORKDIR/tcconf.img"
TCCONF_ARGS=()
case "$TCCONF_SIZE_MB" in
  0) rm -f "$TCCONF_IMG" ;;
  ''|*[!0-9]*) die "TCCONF_SIZE_MB must be 0 or an integer of at least 64" ;;
  *)
    [ "$TCCONF_SIZE_MB" -ge 64 ] \
      || die "TCCONF_SIZE_MB must be 0 or at least 64 (FAT32 minimum)"
    log "stage 4  creating ${TCCONF_SIZE_MB} MiB TCCONF settings partition"
    rm -f "$TCCONF_IMG"
    mkfs.vfat -C -F 32 -n TCCONF "$TCCONF_IMG" \
      $((TCCONF_SIZE_MB * 1024)) >/dev/null
    mmd -i "$TCCONF_IMG" ::/ca-certificates
    mmd -i "$TCCONF_IMG" ::/support
    mcopy -i "$TCCONF_IMG" "$OUTDIR/config.json" ::/config.json
    if [ -n "$SUPPORT_AUTHORIZED_KEYS_FILE" ]; then
      [ -f "$SUPPORT_AUTHORIZED_KEYS_FILE" ] \
        || die "SUPPORT_AUTHORIZED_KEYS_FILE not found: $SUPPORT_AUTHORIZED_KEYS_FILE"
      # Validate every non-comment line before publishing an image that would
      # appear support-ready but cannot actually accept a key login.
      python3 - "$SUPPORT_AUTHORIZED_KEYS_FILE" <<'PYEOF'
import base64, pathlib, sys
path = pathlib.Path(sys.argv[1])
valid = 0
for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    fields = line.split()
    if len(fields) < 2 or not fields[0].startswith(("ssh-", "ecdsa-", "sk-")):
        raise SystemExit("invalid SSH public key at %s:%d" % (path, number))
    try:
        base64.b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise SystemExit("invalid SSH public key at %s:%d: %s" % (path, number, exc))
    valid += 1
if not valid:
    raise SystemExit("SUPPORT_AUTHORIZED_KEYS_FILE contains no public keys")
PYEOF
      mcopy -i "$TCCONF_IMG" "$SUPPORT_AUTHORIZED_KEYS_FILE" \
        ::/support/authorized_keys
      log "stage 4  embedded key-only SSH support access"
    fi
    # With the existing isohybrid GPT option, xorriso records this in both GPT
    # and the bootable MBR. 0x0c is FAT32 LBA in MBR and Basic Data in GPT.
    TCCONF_ARGS=(-append_partition 3 0x0c "$TCCONF_IMG")
    ;;
esac
[ "$TCCONF_SIZE_MB" != "0" ] || [ -z "$SUPPORT_AUTHORIZED_KEYS_FILE" ] \
  || die "SUPPORT_AUTHORIZED_KEYS_FILE needs an embedded TCCONF partition"

# ================================================================ stage 5 ====
# Master the hybrid ISO.
# =============================================================================
log "stage 5  mastering ISO"
mkdir -p "$OUTDIR"
ISO_PATH="$OUTDIR/${IMAGE_NAME}-${DISTRO_VERSION}.iso"
if [ -e "$ISO_PATH" ] && ! rm -f "$ISO_PATH" 2>/dev/null; then
  die "cannot replace $ISO_PATH - something has it open.
     A running virtual machine with this ISO attached is the usual cause;
     Windows will not let the file be replaced while it is mounted.
     Stop the VM (or eject the ISO) and build again."
fi

xorriso -as mkisofs \
  -iso-level 3 -full-iso9660-filenames -joliet -rational-rock \
  -volid "${DISTRO_NAME^^}" \
  -isohybrid-mbr /usr/lib/ISOLINUX/isohdpfx.bin \
  -partition_offset 16 \
  -b isolinux/isolinux.bin -c isolinux/boot.cat \
     -no-emul-boot -boot-load-size 4 -boot-info-table \
  -eltorito-alt-boot -e boot/grub/efi.img -no-emul-boot \
     -isohybrid-gpt-basdat \
  "${TCCONF_ARGS[@]}" \
  -o "$ISO_PATH" "$IMAGE" || die "xorriso failed"

# Rounded file-manager sizes cannot distinguish close releases. Publish an
# exact digest beside every ISO so operators can identify and verify it.
ISO_BASENAME="$(basename "$ISO_PATH")"
(cd "$OUTDIR" && sha256sum "$ISO_BASENAME" > "$ISO_BASENAME.sha256")
log "ISO bytes: $(stat -c %s "$ISO_PATH")"
log "ISO SHA-256: $(cut -d ' ' -f1 "$ISO_PATH.sha256")"

# ================================================================ stage 6 ====
# PXE / netboot artifacts + ready-to-paste server configs.
# =============================================================================
log "stage 6  exporting PXE artifacts"
PXE="$OUTDIR/pxe"
rm -rf "$PXE"; mkdir -p "$PXE/thinclient" "$PXE/pxelinux.cfg" "$PXE/grub"

cp "$IMAGE/live/vmlinuz"            "$PXE/thinclient/vmlinuz"
cp "$IMAGE/live/initrd.img"         "$PXE/thinclient/initrd.img"
cp "$IMAGE/live/filesystem.squashfs" "$PXE/thinclient/filesystem.squashfs"
cp "$IMAGE/live/filesystem.squashfs.sha256" "$PXE/thinclient/filesystem.squashfs.sha256"

# --- BIOS netboot: pxelinux plus the modules it loads at runtime -------------
MISSING_PXE=""
for f in pxelinux.0 ldlinux.c32 libcom32.c32 libutil.c32 menu.c32 vesamenu.c32; do
  found=0
  for d in /usr/lib/PXELINUX /usr/lib/syslinux/modules/bios; do
    [ -f "$d/$f" ] && cp "$d/$f" "$PXE/" && found=1 && break
  done
  [ "$found" = 1 ] || MISSING_PXE="$MISSING_PXE $f"
done
[ -n "$MISSING_PXE" ] && warn "BIOS netboot files missing:$MISSING_PXE (apt install pxelinux syslinux-common)"

# --- UEFI netboot: a GRUB built for the network, with the right prefix -------
# The default netboot image has no http module, so a menu entry that loads the
# kernel over HTTP fails and drops the client back to the GRUB menu, where it
# waits forever. Preload networking and http so the fast path works.
if grub-mknetdir --net-directory="$PXE" --subdir=grub \
                 -d /usr/lib/grub/x86_64-efi \
                 --modules="net efinet tftp http linux normal configfile search echo test all_video gfxterm part_gpt part_msdos fat iso9660" \
                 >/dev/null 2>&1; then
  log "stage 6  UEFI netboot loader: grub/x86_64-efi/core.efi"
else
  warn "grub-mknetdir failed - UEFI PXE clients will need pxe/make-uefi-netboot.sh"
fi

# The signed pair lets UEFI clients netboot with Secure Boot still enabled.
if [ -f "$ROOTFS/usr/lib/shim/shimx64.efi.signed" ] \
   && [ -f "$ROOTFS/usr/lib/grub/x86_64-efi-signed/grubnetx64.efi.signed" ]; then
  cp "$ROOTFS/usr/lib/shim/shimx64.efi.signed"                    "$PXE/bootx64.efi"
  cp "$ROOTFS/usr/lib/grub/x86_64-efi-signed/grubnetx64.efi.signed" "$PXE/grubx64.efi"
  [ -f "$ROOTFS/usr/lib/shim/mmx64.efi.signed" ] && \
    cp "$ROOTFS/usr/lib/shim/mmx64.efi.signed"                    "$PXE/mmx64.efi"
  log "stage 6  Secure Boot netboot loader: bootx64.efi (signed shim)"
else
  cp "$IMAGE/EFI/BOOT/BOOTX64.EFI" "$PXE/bootx64.efi"
  warn "no signed netboot chain - UEFI PXE clients need Secure Boot disabled"
fi

# HTTP fetch keeps the client independent of the boot server once it is up,
# and needs no NFS export. {{HTTP}} is rewritten by pxe/render-configs.sh.
cat > "$PXE/pxelinux.cfg/default" <<EOF
DEFAULT menu.c32
PROMPT 0
TIMEOUT 50
MENU TITLE $DISTRO_NAME $DISTRO_VERSION (network boot)

LABEL live
  MENU LABEL ^Start $DISTRO_NAME
  MENU DEFAULT
  KERNEL thinclient/vmlinuz
  APPEND initrd=thinclient/initrd.img $KERNEL_CMDLINE $CACHE_CMDLINE ip=dhcp fetch=http://{{HTTP}}/thinclient/filesystem.squashfs tc.config=http://{{HTTP}}/config.json

LABEL nfs
  MENU LABEL Start $DISTRO_NAME (^NFS root)
  KERNEL thinclient/vmlinuz
  APPEND initrd=thinclient/initrd.img $KERNEL_CMDLINE ip=dhcp netboot=nfs nfsroot={{HTTP}}:/srv/thinclient

LABEL debug
  MENU LABEL Start with ^diagnostic console
  KERNEL thinclient/vmlinuz
  APPEND initrd=thinclient/initrd.img boot=live components union=overlay tc.debug=1 $CACHE_CMDLINE ip=dhcp fetch=http://{{HTTP}}/thinclient/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
EOF

cat > "$PXE/grub/grub.cfg" <<EOF
set default=0
# If loading the kernel over HTTP fails - an older netboot image without the
# http module, a proxy in the way - fall through to the TFTP entry rather than
# leaving a diskless client sitting at a menu nobody is in front of.
set fallback=1
set timeout=5
set timeout_style=menu
menuentry "Start $DISTRO_NAME - HTTP fast path (recommended)" {
    linux  (http,{{HTTP}})/thinclient/vmlinuz $KERNEL_CMDLINE $CACHE_CMDLINE ip=dhcp fetch=http://{{HTTP}}/thinclient/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
    initrd (http,{{HTTP}})/thinclient/initrd.img
}
menuentry "Start $DISTRO_NAME - TFTP recovery" {
    linux  /thinclient/vmlinuz $KERNEL_CMDLINE $CACHE_CMDLINE ip=dhcp fetch=http://{{HTTP}}/thinclient/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
    initrd /thinclient/initrd.img
}
EOF

cat > "$PXE/boot.ipxe" <<EOF
#!ipxe
dhcp
set base http://{{HTTP}}/thinclient
kernel \${base}/vmlinuz $KERNEL_CMDLINE $CACHE_CMDLINE ip=dhcp fetch=\${base}/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
initrd \${base}/initrd.img
boot
EOF

cp -a "$REPO_DIR/pxe/." "$PXE/" 2>/dev/null || true

# ------------------------------------------------------------------ report ---
ISO_MB=$(( $(stat -c%s "$ISO_PATH") / 1024 / 1024 ))
ELAPSED=$(( $(date +%s) - STAMP_START ))
echo
log "BUILD COMPLETE in $((ELAPSED/60))m $((ELAPSED%60))s"
echo "  ISO        : $ISO_PATH  (${ISO_MB} MB)"
echo "  squashfs   : ${SQUASH_MB} MB"
echo "  kernel     : $KVER"
echo "  Secure Boot: $([ "$SECUREBOOT_OK" = 1 ] && echo 'signed (shim)' || echo 'unsigned - disable Secure Boot')"
echo "  PXE tree   : $PXE"
echo
echo "  Write to USB (Windows) : see README, use Rufus/dd in DD mode"
echo "  Serve over PXE         : bash pxe/render-configs.sh <server-ip>"
