# Autoinstall Configurations

Templates and tools for automated VM provisioning on XCP-NG.

## OpenBSD (firewall/mail)

`openbsd-fw.conf` — autoinstall response file. Serve via HTTP on the mgmt bridge:
`openbsd-mail.conf` is the equivalent response file for the `mail` VM on
xenbr-infra with static IPv6 `2a0c:b641:b50:2::90`.

```bash
# On dom0: start DHCP + HTTP for autoinstall
dhcpd -cf /etc/dhcp/dhcpd.conf xapi0
cd /path/to/autoinstall && python3 -m http.server 80 --bind 10.0.0.1

# Boot VM from ISO, type 'a' at prompt, select mgmt NIC, confirm URL
```

OpenBSD NICs on Xen: `xnf0`, `xnf1`, `xnf2` (not `vio`).
Install sets come from the CD (`Location of sets = cd0`).

## Debian (cloud-init)

For Debian 13 cloud images on XCP-NG:

- `debian-cloud-init.yaml.j2` — user-data template
- `debian-network-config.yaml.j2` — network-config template (netplan v2)
- `debian-meta-data.j2` — meta-data template

Inject into the VM disk at `/var/lib/cloud/seed/nocloud/` with a NoCloud datasource config at `/etc/cloud/cloud.cfg.d/99_nocloud.cfg`:

```yaml
datasource_list: [NoCloud]
datasource:
  NoCloud:
    seedfrom: /var/lib/cloud/seed/nocloud/
```

Debian NIC naming on Xen: `enX0`, `enX1`, etc. Use `match: name: "en*"` in network-config.

## QMP Tools

For interacting with VMs that have no network yet (e.g. during OS install):

- `qmp-keys.py <dom-id> <text>` — send keystrokes via QEMU QMP
- `qmp-screenshot.py <dom-id>` — take VGA screenshot (PPM format)

Before taking screenshots, create the writable tmp dir:
```bash
mkdir -p /var/xen/qemu/root-<dom-id>/tmp
chmod 777 /var/xen/qemu/root-<dom-id>/tmp
```
