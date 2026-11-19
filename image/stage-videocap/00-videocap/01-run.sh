#!/bin/bash -e
# pi-gen runs this on the build host with the image mounted at ROOTFS_DIR. files/src is a copy of the repository,
# put there by the workflow (.github/workflows/image.yml) before the build.
install -d "${ROOTFS_DIR}/opt/videocap-src"
cp -a files/src/. "${ROOTFS_DIR}/opt/videocap-src/"
on_chroot <<'CHROOT'
cd /opt/videocap-src
./install.sh --image
CHROOT
