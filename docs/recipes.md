# Where captures go: recipes

Each recipe below is a set of settings. Put them in a named destination to pick it on the page (`DEST.nas.LABEL=My
NAS`, then `DEST.nas.MASTER_REMOTE=...`), or straight in the settings file for a station that always sends to the same
place. `MP4_REMOTE` and `MASTER_REMOTE` are rclone destinations: `remote:path` for a remote set up with
`sudo rclone config` (videocap runs as root, so its rclone settings are root's), or a plain local folder. Every
upload goes under a temporary `.partial` name and is renamed when complete, so a library never sees half a file.

## A media server's library folder (Jellyfin, Plex, Kodi, Emby)

If the library is on the same network over SMB:

```ini
# sudo rclone config  ->  new remote "library", type smb
[library]
type = smb
host = nas.local
user = videocap
pass = <rclone obscure output>
```

```ini
MP4_REMOTE=library:Media/Home Videos
NFO=1
```

Each MP4 arrives with a `.nfo` (title from your item list, the item ID as sort title, a short description), which
Jellyfin, Kodi and Emby read. Plex ignores `.nfo` files but uses the file name, which is `<id> - <title>.mp4`.

`REPLACE_EXISTING=1` makes a recapture replace the earlier MP4 of the same item: the old video and its subtitles move
to `.replaced/<take>/` beside it (hidden from libraries; nothing is deleted), and the library keeps its description
and artwork.

## Masters to a NAS over SFTP

```ini
[nas]
type = sftp
host = nas.local
user = videocap
key_file = /etc/videocap/nas_ed25519
known_hosts_file = /etc/videocap/known_hosts
```

```ini
MASTER_REMOTE=nas:archive/video-masters
```

Each take gets its own folder: master(s), log, JSON report, then the `.md5` last, so whatever reads the folder knows
the take is complete when the checksum file appears (`md5sum -c` verifies it).

## A write-only drop, with the archive kept elsewhere (the stopover pattern)

For an off-site archive, stations shouldn't hold archive credentials. Instead they push masters to a **stopover**:
an SFTP-only login on another machine that can write and rename but not delete or read anything else, and that
machine moves complete takes (those whose `.md5` has arrived) to the archive on its own schedule, checking them
first. Home upload speed and the archive's quirks then never hold up a capture.

On the stopover (OpenSSH):

```
# /etc/ssh/sshd_config.d/capture-drop.conf
Match User capture-drop
    ChrootDirectory /srv/capture-drop
    ForceCommand internal-sftp -d /incoming -P remove,rmdir
    AllowTcpForwarding no
    X11Forwarding no
    PermitTTY no
```

On the station, an SFTP remote with no shell (so rclone doesn't try to run commands there) that doesn't set file
times:

```ini
[drop]
type = sftp
host = stopover.example.net
user = capture-drop
key_file = /etc/videocap/drop_ed25519
known_hosts_file = /etc/videocap/known_hosts
shell_type = none
set_modtime = false
```

```ini
MASTER_REMOTE=drop:
MASTER_REVIEW=1
```

## Backblaze B2 or S3

```ini
[b2]
type = b2
account = <key id>
key = <application key>
```

```ini
MASTER_REMOTE=b2:my-family-videos/masters
```

For Amazon S3 Glacier Deep Archive, set `storage_class = DEEP_ARCHIVE` in the S3 remote. Deep Archive charges for
early deletion (180 days) and takes 12–48 hours to restore, so keep `MASTER_REVIEW=1` and watch each MP4 before
approving its master.

## Just a bigger local disk

```ini
MASTERS_DIR=/mnt/masters/videocap
```

with the disk mounted at `/mnt/masters` by `/etc/fstab` (by label or UUID, with `nofail`). The folder must already
exist: videocap never creates it, so a disk that isn't mounted can't be mistaken for an empty folder. Share it
read-only on the network with Samba if you want to open masters from another computer, but turn the share off
while recording if other computers read from it heavily.

## No uploads at all

Leave `MASTERS_DIR`, `MP4_REMOTE` and `MASTER_REMOTE` blank: everything stays on the capture disk, and the page says
when it's safe to unplug it.
