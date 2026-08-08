#!/bin/sh
#
# SPDX-FileCopyrightText: 2023 Samuel Tyler <samuel@samuelt.me>
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

# PLX: before bringing up networking, mount sda2 (the amd64 root) at /mnt/amd64 and -- if it carries
# the baked live-bootstrap distfiles (offline mode) -- bind them onto /external/distfiles. Then the
# late-era fetches (from the very next manifest step onward) find every source LOCALLY via
# download_source_line's `[ -e $fname ]` guard and never actually hit the network -- a single-disk,
# fully-offline bootstrap. This mount is REUSED by the PLX amd64 stage (x86.sh does not re-mount
# sda2). Skips cleanly when sda2 has no /lb-distfiles (a normal ONLINE build), so the fetches below
# then run for real. DISK is sda1 here (set by move_disk); sda2 is the sibling partition.
plx_part2="/dev/${DISK%[0-9]}2"
[ -b "$plx_part2" ] || plx_part2="/dev/sda2"
if [ -b "$plx_part2" ]; then
    mkdir -p /mnt/amd64
    grep -q " /mnt/amd64 " /proc/mounts || mount -t ext4 "$plx_part2" /mnt/amd64 2>/dev/null || true
    if [ -d /mnt/amd64/lb-distfiles ]; then
        mkdir -p /external/distfiles
        grep -q " /external/distfiles " /proc/mounts || \
            mount --bind /mnt/amd64/lb-distfiles /external/distfiles
        echo "PLX: /external/distfiles <- sda2:/lb-distfiles (offline live-bootstrap sources)"
    fi
fi

dhcpcd --waitip=4
# Ensure network accessible
timeout=120
while ! curl example.com >/dev/null 2>&1; do
    sleep 1
    # shellcheck disable=SC2219
    let timeout--
    if [ "${timeout}" -le 0 ]; then
        # PLX: do NOT hard-fail here (upstream runs `false`, which drops to the debug trap).
        # Nothing after this step fetches over the network -- live-bootstrap's own sources are
        # baked into the image at build time (lib/generator.py) -- so this is only a connectivity
        # CHECK. Under an OFFLINE run (QEMU restrict=on) it can never pass, so continue instead and
        # let the rest of the bootstrap proceed on its baked distfiles.
        echo "Timeout reached for internet to become accessible (continuing offline)"
        break
    fi
done
