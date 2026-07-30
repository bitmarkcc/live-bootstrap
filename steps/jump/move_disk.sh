#!/bin/sh
#
# SPDX-FileCopyrightText: 2023 Samuel Tyler <samuel@samuelt.me>
#
# SPDX-License-Identifier: GPL-3.0-or-later

set -e
# mount might fail if /etc doesn't exist because of fstab and mtab
mkdir -p /dev /etc
mount -t devtmpfs none /dev &> /junk || true # no /dev/null yet
rm /junk &> /dev/null || true

timeout=120
while ! dd if=/dev/${DISK} of=/dev/null bs=512 count=1; do
    sleep 1
    # shellcheck disable=SC2219
    let timeout--
    if [ "${timeout}" -le 0 ]; then
        echo "Timeout reached for disk to become accessible"
        false
    fi
done

# Create partition if it doesn't exist
# 'stat -c "%T"' prints the minor device type in hexadecimal.
# The decimal version (with "%Lr") is not available in this version of stat.
if [ $((0x$(stat -c "%T" "/dev/${DISK}") % 8)) -eq 0 ]; then
    echo "Creating partition table..."
    # PLX: two partitions instead of one. -S32 -H64 aligns to MiB (cyl = 2048 sectors).
    #   sda1: start 2097152 (1 GiB), size 60817408 sectors (29 GiB) -- IDENTICAL extent to the
    #         original single-partition layout (started at 1 GiB, filled a 30 GiB disk), so the
    #         i686 bootstrap sees no difference. Ends at sector 62914560 (the old 30 GiB end).
    #   sda2: start 62914560, size = rest of the disk (the space beyond the old 30 GiB) -- the
    #         PLX amd64 root. It is ALREADY an ext4 filesystem, pre-populated with the Gentoo
    #         distfiles at /var/cache/distfiles by lib/generator.py (write_plx_distfiles_sda2,
    #         via mkfs.ext4 -E offset=). sfdisk only writes the partition table at sector 0 --
    #         it does NOT touch sda2's data -- so the baked ext4 survives. Do NOT mkfs sda2
    #         here (would erase the distfiles); the amd64 stage MOUNTS it, never reformats it.
    #         If PLX_DISTFILES was empty at build time, sda2 is just unformatted space instead.
    # Requires a disk larger than 30 GiB (run-qemu.sh DISK=64G -> sda2 ~= 34 GiB).
    printf '%s\n' '2097152,60817408' '62914560,' | sfdisk -uS -S32 -H64 --force "/dev/${DISK}"
    fdisk -l "/dev/${DISK}"
    echo "Creating ext4 partition..."
    mkfs.ext4 -F -F "/dev/${DISK}1"          # sda1 only; sda2 is pre-formatted (generator)
    DISK="${DISK}1"
    echo DISK="${DISK}" >> /steps/bootstrap.cfg
fi

# Mount the partition, move everything into /external
mkdir -p /newroot
mount -t ext4 "/dev/${DISK}" /newroot
mkdir -p /newroot/external
mv /newroot/* /newroot/external/ 2>/dev/null || true # obviously errors trying to move external into itself

# Switch root
mkdir -p /rootonly
# This doesn't recursively mount - that's why we're able to copy everything over
mount --bind / /rootonly
cp -ar /rootonly/* /newroot/
sed -e 's/newroot//' /rootonly/etc/mtab | grep -v 'rootonly' > /newroot/etc/mtab
umount /rootonly
switch_root /newroot /init
