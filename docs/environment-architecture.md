# Environment Architecture

This document describes the storage and virtualization layout used for this SMA-NG environment so other operators can understand the design decisions and path conventions.

It is intentionally specific to this deployment, not a generic recommendation for every installation.

## Goals

The environment is designed around a few priorities:

- local writes should land on fast storage first
- shared media should remain visible on every compute node
- Proxmox LXC containers and KVM VMs should see the same effective media layout
- read-heavy media workloads should benefit from caching
- SMA-NG should be able to target stable, predictable mount points regardless of where the actual storage lives

## Hardware Inventory

### Proxmox compute nodes

The Proxmox cluster is built from:

- `3x` ASUS NUC 14 Pro
- `64 GB` RAM each
- `2 TB` NVMe each
- `2 TB` SSD each
- `2x 2.5 GbE` per node for general network connectivity
- `2x 25G Thunderbolt` interconnects per node, arranged in a daisy-chain cluster fabric

The high-speed Thunderbolt interconnect is used for cluster traffic running OpenFabric via FRR.

### Why this matters

This hardware layout explains several design decisions:

- CephFS is practical because the compute nodes have enough local storage and memory to support clustered storage roles
- the Thunderbolt-based 25G fabric gives the storage cluster a much faster east-west path than ordinary management/client LAN traffic
- enough local SSD/NVMe capacity exists on each node to justify a local-first storage tier and virtualization-heavy workload placement
- `64 GB` RAM per node gives room for Proxmox, storage services, cache, and guest workloads without forcing an overly small cluster design

### NAS storage

Shared bulk storage is provided by a QNAP NAS with:

- `8x 8 TB` NAS drives
- `2x 1 TB` NVMe drives used for caching

### Why the NAS is used this way

The QNAP is the large-capacity media backend, while the compute nodes provide the virtualization and clustered compute layer.

This separation is intentional:

- the NAS provides bulk capacity for the media library exported over NFS
- the Proxmox nodes provide compute, virtualization, caching, and the merged namespace seen by workloads
- the NVMe cache on the NAS helps absorb repeated reads and improve NFS-backed media access patterns before the data even reaches the host-side cache layers

## High-Level Layout

```text
CephFS                 NFS media export
   │                         │
   ▼                         ▼
/mnt/local              /mnt/nfs/Media/{TV,Movies,Kids-Movies,Kids-TV}
   │                         │
   └────────────┬────────────┘
                ▼
     mergerfs on Proxmox nodes
     /mnt/local:NC:/mnt/nfs:RW
                │
      ┌─────────┴─────────┐
      ▼                   ▼
  bind mounts         virtiofsd
  into LXC            into KVM VMs
  containers
```

## Storage Components

### CephFS for local-first storage

`/mnt/local` is backed by CephFS.

On the Proxmox VE hosts, this is bound in from:

```text
/mnt/pve/cephfs/local
```

and then exposed to workloads as:

```text
/mnt/local
```

### Why CephFS is used here

CephFS provides:

- shared storage semantics across Proxmox nodes
- consistent paths for containers and VMs
- a suitable landing area for local-first writes before overlaying with other storage

In this environment, `/mnt/local` is the higher-priority branch in the merged filesystem.

### NFS for shared media library paths

NFS is used for the main media tree:

```text
/mnt/nfs/Media/TV
/mnt/nfs/Media/Movies
/mnt/nfs/Media/Kids-Movies
/mnt/nfs/Media/Kids-TV
```

This is the long-lived shared media library layer.

### Why NFS is used here

The NFS layer provides:

- a stable shared media namespace
- simple export/import semantics across hosts
- compatibility with existing media-manager workflows
- clear separation between local-first and shared-library storage roles

### cachefilesd on each Proxmox host

`cachefilesd` runs on each Proxmox host.

This is used to improve NFS-backed read behavior at the host layer, reducing repeated remote reads for hot content and metadata paths.

In this design, caching is host-local rather than relying only on application-level caching.

## mergerfs Layer

`mergerfs` runs on the Proxmox nodes with:

```text
/mnt/local:NC:/mnt/nfs:RW
```

This is the key design decision that defines how the storage tiers interact.

### Interpretation of the branch modes

- `/mnt/local:NC`
  `NC` means "no create" on the CephFS branch through mergerfs
- `/mnt/nfs:RW`
  `RW` means the NFS branch is writable through mergerfs

The practical effect is:

- both branches are visible in the merged namespace
- new file creation through the mergerfs mount is directed away from the `NC` branch
- the NFS branch remains the writable target through the merged filesystem

If you change these branch modes later, document the reason clearly, because this policy directly affects where new files land.

### Why mergerfs is used here

`mergerfs` gives this environment:

- one stable combined path presented to workloads
- explicit write policy without changing application paths
- less application-level awareness of which backing store holds a given file

This is particularly useful for SMA-NG, Sonarr, Radarr, download clients, and post-processing workflows because they can operate against one logical path layout.

## Propagation into Workloads

### LXC containers via bind mounts

The storage paths above are shared into Proxmox containers using bind mounts.

This keeps the container-visible paths aligned with the host-visible layout and avoids duplicating storage logic inside each container.

Benefits:

- simple path consistency
- no separate per-container network mount logic
- easier operational debugging from the host

### KVM VMs via virtiofsd

The same storage is shared into KVM VMs using `virtiofsd`.

This allows VMs to consume the host-managed storage layout without recreating the entire mount stack inside every guest.

Benefits:

- lower friction than re-building CephFS/NFS/mergerfs in every VM
- better path consistency between host and guest
- simpler central control of mount policy on the Proxmox side

## Design Implications for SMA-NG

This environment matters to SMA-NG in a few specific ways.

### 1. Stable paths are critical

SMA-NG path-based config matching, daemon path rewrites, and media-manager callbacks all work best when the visible paths are stable.

That means:

- use the merged/shared path conventions consistently
- avoid mixing host-only and guest-only path variants where possible
- use `path_rewrites` when different systems still see different prefixes

### 2. Local vs shared storage behavior should be deliberate

Because the real storage behavior is defined by CephFS + NFS + mergerfs policy, operators need to know where writes actually land and which branch owns authoritative media content.

Do not assume "visible in the merged mount" means "stored where you expect" without checking branch policy.

### 3. Debugging needs to start from the host storage layer

When something looks wrong in SMA-NG, Sonarr, or Radarr, the problem may actually be in:

- a missing bind mount
- a broken virtiofsd share
- an NFS visibility issue
- a mergerfs branch-policy misunderstanding
- stale or cold cache behavior

Documenting the host storage design prevents application teams from debugging the wrong layer first.

## Operational Notes

When changing this environment, check:

1. Does the path still exist on the Proxmox host?
2. Does the same path exist inside LXCs?
3. Does the same path exist inside KVM VMs?
4. Is the mergerfs branch policy still what applications expect?
5. Is NFS still cached on each Proxmox node as intended?
6. Do SMA-NG `path_configs` and `path_rewrites` still match reality?

## Suggested SMA-NG Documentation Conventions

When documenting configs for this environment:

- refer to the effective workload-visible paths, not only the backing mounts
- note whether a path is host-only, container-visible, or VM-visible
- call out when a path is coming from CephFS, NFS, or the merged layer
- keep one canonical example for media roots and reuse it everywhere

Example convention:

```text
/mnt/local          -> CephFS-backed host-local branch
/mnt/nfs            -> NFS-backed shared branch
/mnt/<merged-path>  -> workload-facing merged namespace
```

If a future maintainer changes the merged namespace path, update this document before changing SMA-NG config examples.
