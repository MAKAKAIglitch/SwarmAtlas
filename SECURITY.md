# Security and safety policy

## Default behavior

SwarmAtlas is a passive, read-only monitor.

Allowed operations in the current prototype:
- Read network interface counters and interface state through psutil.
- Read the operating system's TCP/UDP connection-owner tables through the optional Rust extension (Windows IP Helper APIs), or through the Python psutil fallback.
- Read process IDs and, when permitted, process names for observed connections.
- Read local UDP bind ownership (PID/local address/local port); this table does not expose the remote IP for every UDP datagram.
- Read a local GeoIP .mmdb file selected by the user.
- Write SwarmAtlas settings and statistics to the current user's application-data directory.
- Render local statistics in the desktop UI.

Operations explicitly out of scope for the default monitor:
- Installing or loading drivers, services, scheduled tasks, or startup hooks.
- Installing Npcap, WinPcap, WinDivert, NDIS filters, or packet-capture components.
- Adding/removing Windows Filtering Platform filters or firewall rules.
- Editing routes, DNS, proxy, IP addresses, adapter settings, or Winsock configuration.
- Capturing packet payloads or transmitting user traffic.
- Automatically requesting elevation or bypassing access restrictions.

## Privileges

The app should run as a normal Windows user. It must not request administrator privileges. If Windows denies access to some socket metadata, show an informative status and continue interface-level monitoring.

## Network behavior

The monitor itself should not bind/listen on a port or create tunnels. The current prototype makes no remote GeoIP requests and has no telemetry endpoint. The Rust extension calls GetExtendedTcpTable and GetExtendedUdpTable for read-only OS metadata; it does not install filters, hooks, drivers or services. If the native extension is absent or an API call is unavailable, the Python compatibility collector remains active.

## Local data

Observed public IPs, process IDs/names, GeoIP metadata, settings and traffic history remain in the user's local SwarmAtlas data directory. They are not uploaded to a SwarmAtlas service. The update helper only contacts GitHub when the user runs it; the first-run package installer downloads its declared dependencies from the configured package index.

## Future features

Per-flow byte accounting is not implemented. Before selecting a backend, evaluate its privilege requirements, whether it installs filters/drivers, whether it can affect packet processing, its CPU/memory overhead, and how it is removed. Any such feature must be a separate, disabled-by-default module with explicit user consent and a documented rollback path. The read-only interface-counter mode must remain available independently.

## Reporting

Please report security or safety issues through GitHub Issues without posting private IPs, peer lists, credentials, or GeoIP database files.
