#!/usr/bin/env python3
"""
This file contains all code required to generate the boot image for live-bootstrap
"""
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2022-2023 Dor Askayo <dor.askayo@gmail.com>
# SPDX-FileCopyrightText: 2021 Andrius Štikonas <andrius@stikonas.eu>
# SPDX-FileCopyrightText: 2021 Melg Eight <public.melg8@gmail.com>
# SPDX-FileCopyrightText: 2021-23 Samuel Tyler <samuel@samuelt.me>

import hashlib
import os
import random
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import traceback

import requests

# pylint: disable=too-many-instance-attributes
class Generator():
    """
    Class responsible for generating the basic media to be consumed.
    """

    git_dir = os.path.join(os.path.dirname(os.path.join(__file__)), '..')
    distfiles_dir = os.path.join(git_dir, 'distfiles')

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(self, arch, external_sources, early_preseed, repo_path, mirrors):
        self.arch = arch
        self.early_preseed = early_preseed
        self.external_sources = external_sources
        self.repo_path = repo_path
        self.mirrors = mirrors
        self.source_manifest = self.get_source_manifest(not self.external_sources)
        self.early_source_manifest = self.get_source_manifest(True)
        self.target_dir = None
        self.external_dir = None

    def reuse(self, target):
        """
        Reuse a previously prepared bwrap environment for further stages.
        """
        self.target_dir = target.path
        self.external_dir = os.path.join(self.target_dir, 'external')
        self.distfiles()

    def prepare(self, target, using_kernel=False, kernel_bootstrap=False, target_size=0):
        """
        Prepare basic media of live-bootstrap.
        /steps -- contains steps to be built
        / -- contains seed to allow steps to be built, containing custom
             scripts and stage0-posix
        """
        self.target_dir = target.path
        self.external_dir = os.path.join(self.target_dir, 'external')

        # We use ext3 here; ext4 actually has a variety of extensions that
        # have been added with varying levels of recency
        # Linux 4.9.10 does not support a bunch of them
        # Attempting to disable extensions that a particular e2fsprogs
        # is *unaware* of causes the filesystem creation to fail
        # We could hypothetically detect e2fsprogs version and create an
        # argument matrix ... or we could just use ext3 instead which
        # is effectively universally the same
        if kernel_bootstrap:
            self.target_dir = os.path.join(self.target_dir, 'init')
            os.mkdir(self.target_dir)

            if not self.repo_path and not self.external_sources:
                self.external_dir = os.path.join(self.target_dir, 'external')
        elif using_kernel:
            self.target_dir = os.path.join(self.target_dir, 'disk')
            self.external_dir = os.path.join(self.target_dir, 'external')

        if self.early_preseed:
            # Extract tar containing preseed
            with tarfile.open(self.early_preseed, "r") as seed:
                seed.extractall(self.target_dir)
            if os.path.exists(os.path.join(self.target_dir, 'steps')):
                shutil.rmtree(os.path.join(self.target_dir, 'steps'))
            if os.path.exists(self.external_dir):
                shutil.rmtree(self.external_dir)
            shutil.copy2(os.path.join(self.git_dir, 'seed', 'preseeded.kaem'),
                         os.path.join(self.target_dir, 'kaem.x86'))
        else:
            self.stage0_posix(kernel_bootstrap)
            self.seed()

        os.makedirs(self.external_dir)

        self.steps()

        self.distfiles()

        if self.repo_path:
            repo_dir = os.path.join(self.external_dir, 'repo-preseeded')
            shutil.copytree(self.repo_path, repo_dir)

        if kernel_bootstrap:
            self.create_builder_hex0_disk_image(self.target_dir + '.img', target_size)

            if self.repo_path or self.external_sources:
                mkfs_args = ['-d', os.path.join(target.path, 'external')]
                target.add_disk("external", filesystem="ext3", mkfs_args=mkfs_args)
        elif using_kernel:
            mkfs_args = ['-F', '-d', os.path.join(target.path, 'disk')]
            target.add_disk("disk",
                            filesystem="ext3",
                            size=(str(target_size) + "M") if target_size else "16G",
                            bootable=True,
                            mkfs_args=mkfs_args)

    def steps(self):
        """Copy in steps."""
        self.get_packages()

        shutil.copytree(os.path.join(self.git_dir, 'steps'), os.path.join(self.target_dir, 'steps'))

    def stage0_posix(self, kernel_bootstrap=False):
        """Copy in all of the stage0-posix"""
        stage0_posix_base_dir = os.path.join(self.git_dir, 'seed', 'stage0-posix')
        for entry in os.listdir(stage0_posix_base_dir):
            if kernel_bootstrap and entry == 'bootstrap-seeds':
                continue
            orig = os.path.join(stage0_posix_base_dir, entry)
            target = os.path.join(self.target_dir, entry)
            if os.path.isfile(orig):
                shutil.copy2(orig, target)
            else:
                shutil.copytree(orig, target)

        if not kernel_bootstrap:
            arch = stage0_arch_map.get(self.arch, self.arch)
            kaem_optional_seed = os.path.join(self.git_dir, 'seed', 'stage0-posix',
                                              'bootstrap-seeds', 'POSIX', arch,
                                              'kaem-optional-seed')
            shutil.copy2(kaem_optional_seed, os.path.join(self.target_dir, 'init'))

    def seed(self):
        """Copy in extra seed files"""
        seed_dir = os.path.join(self.git_dir, 'seed')
        for entry in os.listdir(seed_dir):
            if os.path.isfile(os.path.join(seed_dir, entry)):
                shutil.copy2(os.path.join(seed_dir, entry), os.path.join(self.target_dir, entry))

    def distfiles(self):
        """Copy in distfiles"""
        def copy_no_network_distfiles(out, early):
            # Note that "no disk" implies "no network" for kernel bootstrap mode
            manifest = self.early_source_manifest if early else self.source_manifest
            for file in manifest:
                file = file[3].strip()
                shutil.copy2(os.path.join(self.distfiles_dir, file),
                             os.path.join(out, file))

        early_distfile_dir = os.path.join(self.target_dir, 'external', 'distfiles')
        main_distfile_dir = os.path.join(self.external_dir, 'distfiles')

        if early_distfile_dir != main_distfile_dir:
            os.makedirs(early_distfile_dir, exist_ok=True)
            copy_no_network_distfiles(early_distfile_dir, True)

        if self.external_sources:
            shutil.copytree(self.distfiles_dir, main_distfile_dir, dirs_exist_ok=True)
        else:
            os.mkdir(main_distfile_dir)
            copy_no_network_distfiles(main_distfile_dir, False)

    @staticmethod
    def output_dir(srcfs_file, dirpath):
        """Add a directory to srcfs file system"""
        srcline = f"src 0 {dirpath}\n"
        srcfs_file.write(srcline.encode())

    @staticmethod
    def output_file(srcfs_file, filepath):
        """Add a file to srcfs file system"""
        srcline = f"src {os.path.getsize(filepath)} {filepath}\n"
        srcfs_file.write(srcline.encode())
        with open(filepath, 'rb') as srcfile:
            srcfs_file.write(srcfile.read())

    def output_tree(self, srcfs_file, treepath):
        """Add a tree of files to srcfs file system"""
        self.output_dir(srcfs_file, treepath)
        for root, dirs, files in os.walk(treepath):
            if ".git" in root:
                continue
            for dirpath in dirs:
                if ".git" in dirpath:
                    continue
                self.output_dir(srcfs_file, os.path.join(root, dirpath))

            for filepath in files:
                if ".git" in filepath:
                    continue
                self.output_file(srcfs_file, os.path.join(root, filepath))

    def append_srcfs(self, image_file):
        """Append srcfs file system to disk image"""
        save_cwd = os.getcwd()

        os.chdir(self.target_dir)
        self.output_tree(image_file, '.')

        # Add commands to kick off stage0-posix
        cmd = ' '.join(['src',
                        '0',
                        '/bootstrap-seeds\n'])
        image_file.write(cmd.encode())
        cmd = ' '.join(['src',
                        '0',
                        '/bootstrap-seeds/POSIX\n'])
        image_file.write(cmd.encode())
        cmd = ' '.join(['src',
                        '0',
                        '/bootstrap-seeds/POSIX/x86\n'])
        image_file.write(cmd.encode())
        cmd = ' '.join(['hex0',
                        '/x86/hex0_x86.hex0',
                        '/bootstrap-seeds/POSIX/x86/hex0-seed\n'])
        image_file.write(cmd.encode())
        cmd = ' '.join(['hex0',
                        '/x86/kaem-minimal.hex0',
                        '/bootstrap-seeds/POSIX/x86/kaem-optional-seed\n'])
        image_file.write(cmd.encode())
        cmd = ' '.join(['hex0',
                        '/x86/kaem-minimal.hex0',
                        '/init\n'])
        image_file.write(cmd.encode())
        cmd = ' '.join(['/bootstrap-seeds/POSIX/x86/kaem-optional-seed', '/kaem.x86\n'])
        image_file.write(cmd.encode())
        os.chdir(save_cwd)

    def create_builder_hex0_disk_image(self, image_file_name, size):
        """Create builder-hex0 disk image"""
        with open(image_file_name, 'ab') as image_file:
            # Compile and write stage1 binary seed
            with open(os.path.join('builder-hex0', 'builder-hex0-x86-stage1.hex0'),
                      encoding="utf-8") as infile:
                for line in infile:
                    image_file.write(bytes.fromhex(line.split('#')[0].split(';')[0].strip()))
            # Append stage2 hex0 source
            with open(os.path.join('builder-hex0', 'builder-hex0-x86-stage2.hex0'),
                      encoding="utf-8") as infile:
                image_file.write(infile.read().encode())

        # Close first with statement before getting file size.
        with open(image_file_name, 'ab') as image_file:
            # Pad to next sector
            current_size = os.stat(image_file_name).st_size
            while current_size % 512 != 0:
                image_file.write(b'\0')
                current_size += 1
            self.append_srcfs(image_file)

        current_size = os.stat(image_file_name).st_size

        megabyte = 1024 * 1024
        # fill file with zeros up to next megabyte
        extra = current_size % megabyte
        round_up = megabyte - extra
        with open(image_file_name, 'ab') as image_file:
            image_file.write(b'\0' * round_up)
        current_size += round_up

        # extend file up to desired size
        if current_size < size * megabyte:
            with open(image_file_name, 'ab') as image_file:
                image_file.truncate(size * megabyte)

        # PLX: pre-populate sda2 (distfiles + scripts + repo snapshot) as a ready-made ext4 fs
        # (NOT via the srcfs, which builder-hex0 would grind through bare-metal). See the method.
        self.write_plx_sda2(image_file_name, size)

    @staticmethod
    def write_plx_sda2(image_file_name, size_mb):
        """PLX: pre-populate sda2 (the future amd64 root) as a ready-made ext4 filesystem so the
        bootstrap is FULLY self-contained -- no host, no network, as on the real KGPE-D16 --
        WITHOUT routing anything through builder-hex0's slow srcfs. Written straight into sda2's
        region of the image via `mkfs.ext4 -E offset=` (move_disk.sh, PLX patch, defines the
        partition at this same offset and does NOT mkfs it):
          /var/cache/distfiles/<tarballs>   <- PLX_DISTFILES  (the Gentoo @system sources)
          /root/tmp/bootstrap-amd64/        <- PLX_SCRIPTS     (the PLX bootstrap scripts, minus
                                                                their own distfiles/ subdir)
          /root/tmp/<basename>              <- PLX_SNAPSHOT    (the Gentoo ebuild repo snapshot)
          /lb-distfiles/<tarballs>          <- PLX_LB_DISTFILES (live-bootstrap's OWN sources; PLX's
                                                                 move_disk.sh copies these into
                                                                 /external/distfiles at boot so LB's
                                                                 late-era fetches read them locally
                                                                 -- a single-disk offline bootstrap)
        Any unset/missing env var => that piece is skipped; nothing to write => no-op.
        """
        plx_distfiles = os.environ.get('PLX_DISTFILES')
        plx_scripts   = os.environ.get('PLX_SCRIPTS')
        plx_snapshot  = os.environ.get('PLX_SNAPSHOT')
        plx_lb_distfiles = os.environ.get('PLX_LB_DISTFILES')
        do_distfiles = bool(plx_distfiles and os.path.isdir(plx_distfiles))
        do_scripts   = bool(plx_scripts and os.path.isdir(plx_scripts))
        do_snapshot  = bool(plx_snapshot and os.path.isfile(plx_snapshot))
        do_lb_distfiles = bool(plx_lb_distfiles and os.path.isdir(plx_lb_distfiles))
        if not (do_distfiles or do_scripts or do_snapshot or do_lb_distfiles):
            return
        # MUST match sda2's start sector in steps/jump/move_disk.sh: 62914560 (30 GiB).
        sda2_offset = 62914560 * 512
        if sda2_offset >= size_mb * 1024 * 1024:
            raise ValueError(
                f"disk ({size_mb} MiB) too small for sda2 at offset {sda2_offset} B -- "
                "use a bigger DISK (>= ~32G) or unset the PLX_* vars")
        print(f"PLX: building sda2 ext4 @ offset {sda2_offset}")
        # Stage the fs contents on the SAME fs as the image (target dir); hardlink the (large)
        # distfiles from PLX_DISTFILES when possible to avoid duplicating GBs / a /tmp tmpfs.
        content = tempfile.mkdtemp(prefix='plx-sda2-', dir=os.path.dirname(image_file_name))
        try:
            if do_distfiles:
                distdir = os.path.join(content, 'var', 'cache', 'distfiles')
                os.makedirs(os.path.dirname(distdir))
                try:
                    shutil.copytree(plx_distfiles, distdir, copy_function=os.link)
                except OSError:
                    shutil.copytree(plx_distfiles, distdir)
                print(f"  + /var/cache/distfiles <- {plx_distfiles}")
            if do_scripts:
                roottmp = os.path.join(content, 'root', 'tmp')
                os.makedirs(roottmp, exist_ok=True)
                name = os.path.basename(os.path.normpath(plx_scripts))   # e.g. bootstrap-amd64
                # skip the scripts' own distfiles/ subdir -- already written above, and huge.
                shutil.copytree(plx_scripts, os.path.join(roottmp, name),
                                ignore=shutil.ignore_patterns('distfiles'))
                print(f"  + /root/tmp/{name} <- {plx_scripts} (minus distfiles/)")
            if do_snapshot:
                roottmp = os.path.join(content, 'root', 'tmp')
                os.makedirs(roottmp, exist_ok=True)
                shutil.copy2(plx_snapshot, os.path.join(roottmp, os.path.basename(plx_snapshot)))
                print(f"  + /root/tmp/{os.path.basename(plx_snapshot)} <- {plx_snapshot}")
            if do_lb_distfiles:
                # live-bootstrap's OWN source tarballs (bash-5.2.15.tar.gz, ...) -> sda2:/lb-distfiles.
                # PLX's get_network.sh patch mounts sda2 at /mnt/amd64 and bind-mounts /lb-distfiles
                # onto /external/distfiles just before the first network fetch, so download_source_line
                # finds every source locally ([ -e $fname ]) and never fetches -- one disk, fully
                # offline (no separate --external-sources disk). Hardlink to avoid duplicating GBs.
                lbdir = os.path.join(content, 'lb-distfiles')
                try:
                    shutil.copytree(plx_lb_distfiles, lbdir, copy_function=os.link)
                except OSError:
                    shutil.copytree(plx_lb_distfiles, lbdir)
                print(f"  + /lb-distfiles <- {plx_lb_distfiles}")
            # -E offset= starts the fs at sda2's offset and fills to the image end (== sda2's
            # extent, since move_disk gives sda2 "rest of disk"). Disable ext4 features newer
            # than live-bootstrap's ~4.14 kernel, which also has to mount sda2 during the i686
            # stage (orphan_file: 5.15, metadata_csum_seed: 4.15).
            # Run UNDER fakeroot with the staging chowned to 0:0: mkfs -d preserves the staging's
            # uid/gid, and this generator runs UNPRIVILEGED as the host user -- so WITHOUT fakeroot
            # the baked /var,/root,... come out host-owned, and systemd-tmpfiles' "unsafe path
            # transition" guard then fails on the amd64 system's first boot. fakeroot fakes root
            # ownership so mkfs bakes root-owned dirs (needs `fakeroot` on the host PATH).
            mkfs = (f"chown -R 0:0 {shlex.quote(content)} && "
                    f"mkfs.ext4 -F -O ^orphan_file,^metadata_csum_seed "
                    f"-E offset={sda2_offset} -d {shlex.quote(content)} "
                    f"{shlex.quote(image_file_name)}")
            subprocess.run(['fakeroot', 'sh', '-c', mkfs], check=True)
        finally:
            shutil.rmtree(content, ignore_errors=True)

    @staticmethod
    def check_file(file_name, expected_hash):
        """Check hash of downloaded source file."""
        with open(file_name, "rb") as downloaded_file:
            downloaded_content = downloaded_file.read() # read entire file as bytes
        readable_hash = hashlib.sha256(downloaded_content).hexdigest()
        if expected_hash == readable_hash:
            return
        raise ValueError(f"Checksum mismatch for file {os.path.basename(file_name)}:\n\
expected: {expected_hash}\n\
actual:   {readable_hash}\n\
When in doubt, try deleting the file in question -- it will be downloaded again when running \
this script the next time")

    def download_file(self, url, directory, file_name, silent=False):
        """
        Download a single source archive.
        """
        abs_file_name = os.path.join(directory, file_name)

        # Create a directory for downloaded file
        if not os.path.isdir(directory):
            os.mkdir(directory)

        # Actually download the file
        headers = {
                "Accept-Encoding": "identity",
                "User-Agent": "curl/7.88.1"
        }
        if not os.path.isfile(abs_file_name):
            if not silent:
                print(f"Downloading: {file_name}")

            def do_download(source):
                response = requests.get(source, allow_redirects=True, stream=True,
                        headers=headers, timeout=20)
                if response.status_code == 200:
                    with open(abs_file_name, 'wb') as target_file:
                        target_file.write(response.raw.read())
                    return True
                print(f"Download failed from {option}: {response.status_code} {response.reason}")
                return False

            done = False
            if self.mirrors:
                options = [f"{x}/{file_name}" for x in self.mirrors]
            else:
                options = []
            random.shuffle(options)
            for option in options:
                if do_download(option):
                    done = True
                    break

            if not done:
                if url == "_" or not do_download(url):
                    raise requests.RequestException(f"Unable to download {url} from ",
                                                    "any mirror or original")

        return abs_file_name

    def get_packages(self):
        """Prepare remaining sources"""
        for line in self.source_manifest:
            try:
                path = self.download_file(line[2], line[1], line[3])
            except requests.HTTPError:
                print(traceback.format_exc())
        for line in self.source_manifest:
            path = os.path.join(line[1], line[3])
            self.check_file(path, line[0])

    @classmethod
    def get_source_manifest(cls, pre_network=False):
        """
        Generate a source manifest for the system.
        """
        entries = []
        directory = os.path.relpath(cls.distfiles_dir, cls.git_dir)

        # Find all source files
        steps_dir = os.path.join(cls.git_dir, 'steps')
        with open(os.path.join(steps_dir, 'manifest'), 'r', encoding="utf_8") as file:
            for line in file:
                if pre_network and line.strip().startswith("improve: ") and "network" in line:
                    break

                if not line.strip().startswith("build: "):
                    continue

                step = line.split(" ")[1].split("#")[0].strip()
                sourcef = os.path.join(steps_dir, step, "sources")
                if os.path.exists(sourcef):
                    # Read sources from the source file
                    with open(sourcef, "r", encoding="utf_8") as sources:
                        for source in sources.readlines():
                            source = source.strip().split(" ")

                            if source[0] == "g" or source[0] == "git":
                                source[1:] = source[2:]

                            if len(source) > 3:
                                file_name = source[3]
                            else:
                                # Automatically determine file name based on URL.
                                file_name = os.path.basename(source[1])

                            entry = (source[2], directory, source[1], file_name)
                            if entry not in entries:
                                entries.append(entry)

        return entries

stage0_arch_map = {
    "amd64": "AMD64",
}
