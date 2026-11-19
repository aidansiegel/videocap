#!/bin/bash -e
# pi-gen: start this stage from the previous one's root filesystem (Raspberry Pi OS Lite, from stage2).
if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi
