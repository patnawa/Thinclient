#!/bin/sh
# Restore a PXE squashfs from a checksum-addressed removable USB cache.
# This file is sourced by live-boot after 9990-mount-http.sh. The build renames
# Debian's original do_httpmount() to do_httpmount_network(), then this wrapper
# tries the cache before falling back to the unchanged network implementation.

tc_cache_arg()
{
	key="$1"
	for parameter in ${LIVE_BOOT_CMDLINE:-$(cat /proc/cmdline)}
	do
		case "$parameter" in
			"${key}"=*) printf '%s\n' "${parameter#*=}"; return 0 ;;
		esac
	done
	return 1
}

tc_cache_parameters()
{
	[ "$(tc_cache_arg tc.cache 2>/dev/null || true)" = "1" ] || return 1
	TC_CACHE_PROFILE="$(tc_cache_arg tc.cache.profile 2>/dev/null || true)"
	TC_CACHE_SHA256="$(tc_cache_arg tc.cache.sha256 2>/dev/null || true)"
	[ -z "${TC_TRUST_SHA256:-}" ] || TC_CACHE_SHA256="$TC_TRUST_SHA256"
	TC_CACHE_LABEL="$(tc_cache_arg tc.cache.label 2>/dev/null || echo TCCACHE)"
	TC_CACHE_SHA256="$(printf '%s' "$TC_CACHE_SHA256" | tr 'A-F' 'a-f')"

	printf '%s' "$TC_CACHE_PROFILE" | grep -Eq '^[A-Za-z0-9._-]{1,32}$' || return 1
	printf '%s' "$TC_CACHE_LABEL" | grep -Eq '^[A-Za-z0-9._-]{1,32}$' || return 1
	printf '%s' "$TC_CACHE_SHA256" | grep -Eq '^[0-9a-f]{64}$' || return 1
	[ -n "${FETCH:-}" ] || return 1
	return 0
}

tc_cache_is_usb()
{
	udevadm info --query=property --name="$1" 2>/dev/null \
		| grep -qx 'ID_BUS=usb'
}

tc_cache_usb_present()
{
	# A storage interface is visible before its block node on slow USB media.
	for interface in /sys/bus/usb/devices/*/bInterfaceClass
	do
		[ -f "$interface" ] || continue
		grep -qx '08' "$interface" && return 0
	done
	return 1
}

tc_cache_devices()
{
	# Do not scan every internal disk or sleep five times on diskless clients
	# without USB storage. An unfinished udev queue or a storage interface keeps
	# the original bounded discovery window for late-enumerating media. The
	# explicit wait override remains available for unusual controller firmware.
	if udevadm settle --timeout=1 2>/dev/null \
		&& [ "$(tc_cache_arg tc.cache.wait 2>/dev/null || true)" != 1 ] \
		&& ! tc_cache_usb_present
	then
		return 1
	fi
	tries=0
	while [ "$tries" -lt 5 ]
	do
		udevadm settle --timeout=1 2>/dev/null || true
		devices="$(blkid -t "LABEL=${TC_CACHE_LABEL}" -o device 2>/dev/null || true)"
		[ -n "$devices" ] && { printf '%s\n' "$devices"; return 0; }
		tries=$((tries + 1))
		sleep 1
	done
	return 1
}

tc_cache_record()
{
	mkdir -p /run/initramfs
	{
		printf 'state=%s\n' "$1"
		printf 'profile=%s\n' "$TC_CACHE_PROFILE"
		printf 'sha256=%s\n' "$TC_CACHE_SHA256"
		printf 'label=%s\n' "$TC_CACHE_LABEL"
		[ -z "${2:-}" ] || printf 'device=%s\n' "$2"
	} > /run/initramfs/tc-cache-status
}

tc_cache_restore()
{
	tc_cache_parameters || return 1
	cache_mount=/run/live/tc-cache
	cache_name="${TC_CACHE_SHA256}.squashfs"
	destination="${mountpoint}/${LIVE_MEDIA_PATH}/$(basename "$FETCH")"
	mkdir -p "$cache_mount"

	for cache_device in $(tc_cache_devices 2>/dev/null || true)
	do
		tc_cache_is_usb "$cache_device" || continue
		cache_type="$(blkid -s TYPE -o value "$cache_device" 2>/dev/null || true)"
		case "$cache_type" in vfat|exfat|ext4) ;; *) continue ;; esac
		mount -t "$cache_type" -o ro,nosuid,nodev,noexec,noatime \
			"$cache_device" "$cache_mount" 2>/dev/null || continue
		cache_file="$cache_mount/thinclient-cache/$TC_CACHE_PROFILE/$cache_name"
		if [ -f "$cache_file" ]; then
			echo "ThinClient cache: verifying $TC_CACHE_PROFILE from $cache_device" > /dev/console
			mount -t ramfs ram "$mountpoint" 2>/dev/null || {
				umount "$cache_mount" 2>/dev/null || true
				continue
			}
			mkdir -p "$(dirname "$destination")"
			# Hash the destination only after the copy succeeds. Hashing tee's
			# stdout can hide a failed destination write.
			actual=""
			if cp "$cache_file" "$destination"; then
				actual="$(sha256sum "$destination")" || actual=""
				actual="${actual%% *}"
			fi
			if [ "$actual" = "$TC_CACHE_SHA256" ]; then
				tc_cache_record hit "$cache_device"
				umount "$cache_mount" 2>/dev/null || true
				echo "ThinClient cache: verified; network root download skipped" > /dev/console
				return 0
			fi
			echo "ThinClient cache: checksum mismatch; downloading a clean copy" > /dev/console
			rm -f "$destination"
			umount "$mountpoint" 2>/dev/null || true
		fi
		umount "$cache_mount" 2>/dev/null || true
	done

	tc_cache_record miss
	return 1
}

tc_verify_release_manifest()
{
	[ -f /etc/thinclient/release.pub ] || return 0
	# The verifier and its public key must themselves arrive via a trusted
	# initramfs. A signature inside an attacker-replaceable initrd is not a
	# complete Secure Boot chain; deployment documentation states this boundary.
	mkdir -p /run/initramfs
	base="${FETCH%/*}"
	wget -q -T 5 -O /run/initramfs/tc-manifest "$base/manifest.sha256" || return 1
	wget -q -T 5 -O /run/initramfs/tc-manifest.sig "$base/manifest.sha256.sig" || return 1
	openssl dgst -sha256 -verify /etc/thinclient/release.pub \
		-signature /run/initramfs/tc-manifest.sig /run/initramfs/tc-manifest \
		>/dev/null 2>&1 || return 1
	TC_TRUST_SHA256="$(awk '$2 == "filesystem.squashfs" {print $1}' /run/initramfs/tc-manifest)"
	printf '%s' "$TC_TRUST_SHA256" | grep -Eq '^[0-9a-f]{64}$'
}

do_httpmount()
{
	if ! tc_verify_release_manifest; then
		echo "ThinClient: release signature could not be verified; refusing root image" > /dev/console
		return 1
	fi
	if tc_cache_restore
	then
		return 0
	fi

	do_httpmount_network
	rc=$?
	if [ "$rc" -eq 0 ] && [ -n "${TC_TRUST_SHA256:-}" ]; then
		root_file="${mountpoint}/${LIVE_MEDIA_PATH}/$(basename "$FETCH")"
		root_hash="$(sha256sum "$root_file")" || return 1
		[ "${root_hash%% *}" = "$TC_TRUST_SHA256" ] || {
			echo "ThinClient: downloaded root does not match signed release" > /dev/console
			return 1
		}
	fi
	if [ "$rc" -eq 0 ] && tc_cache_parameters
	then
		tc_cache_record network
	fi
	return "$rc"
}
