#!/bin/sh
export HOME=/root   # after.sh runs us with a minimal env; portage & ~/-relative paths key off HOME
#
# PLX: auto-run the WHOLE amd64 bootstrap, hands-off, right after live-bootstrap finishes.
#
# live-bootstrap's improve/after.sh runs every /steps/after/*.sh (this hook) BEFORE it drops to the
# interactive shell. By then sda2 -- the PLX amd64 root, carrying the baked bootstrap scripts -- is
# already mounted at /mnt/amd64 (the get_network.sh patch mounts it). So point /root/tmp at sda2 and
# run the full chain unattended:
#     x86.sh (i686 @system, 01-08)  ->  chroot-enter.sh 'x86-gentoo.sh' (amd64 cross, 09-12)  ->  reboot
# On SUCCESS it reboots into the amd64 disk (GRUB -> kernel -> first-boot @system -> login). If ANY
# stage fails it does NOT reboot -- it just returns, and after.sh opens the interactive shell so you
# can debug (both x86.sh and x86-gentoo.sh run under `set -e`, so a hard failure exits non-zero).
#
# Skips cleanly on a plain live-bootstrap run (no PLX scripts baked), and honours an opt-out marker
# (touch /mnt/amd64/.plx-no-autorun to bake in "drop me straight to the shell instead").

[ -d /mnt/amd64/root/tmp/bootstrap-amd64 ] || exit 0   # not a PLX run -> plain live-bootstrap shell
[ -e /mnt/amd64/.plx-no-autorun ] && exit 0            # user opt-out

rm -rf /root/tmp 2>/dev/null
ln -s /mnt/amd64/root/tmp /root/tmp
cd /root/tmp/bootstrap-amd64 || exit 0

echo ">> PLX: live-bootstrap done -- running the full amd64 bootstrap hands-off ..."
if bash x86.sh \
   && bash chroot-enter.sh 'cd /root/tmp/bootstrap-amd64 && bash x86-gentoo.sh'; then
    echo ">> PLX: i686 + amd64 cross stages complete -- rebooting into the amd64 disk"
    bash 13-reboot.sh
else
    echo ">> PLX: a bootstrap stage FAILED -- NOT rebooting."
    echo ">>   after.sh will open a shell; debug, then finish by hand:"
    echo ">>   bash chroot-enter.sh -> bash x86-gentoo.sh -> exit -> bash 13-reboot.sh"
fi
