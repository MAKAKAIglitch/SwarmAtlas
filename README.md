# SwarmAtlas

**Map your share.** A lightweight, local-first desktop prototype for understanding network activity and the public endpoints your PC is connected to.

## Safety model: read-only by default

SwarmAtlas is a passive monitor. The current prototype only reads operating-system interface counters and the OS socket table.

It does not:
- install kernel drivers, NDIS filters, packet-capture drivers, services, scheduled tasks, or startup entries;
- install or configure WinDivert, Npcap, WinPcap, WFP filters/callouts, or firewall rules;
- change IP addresses, routes, DNS, proxy settings, adapter configuration, or Windows network services;
- open listening ports, create network tunnels, or send traffic on behalf of other applications;
- capture, inspect, or save packet payloads;
- require administrator privileges for interface counters;
- upload IP addresses, telemetry, or usage statistics to SwarmAtlas servers.

If Windows restricts access to the socket table, endpoint discovery may be incomplete. Interface byte counters continue to work. SwarmAtlas does not bypass OS permissions or elevate itself.

The optional GeoIP feature reads a user-selected local .mmdb database. The application does not download it.

Any future feature that needs packet capture or per-flow byte accounting must be separately reviewed, explicitly opt-in, and isolated from the default read-only monitor. No driver or network-filter installation will happen silently.

## Prototype status

| Feature | Status |
|---|---|
| Per-interface upload/download totals | Implemented |
| Current upload/download rate | Implemented |
| Persistent local history (SQLite) | Implemented |
| Active public remote endpoints | Implemented |
| Offline country/city lookup using local GeoIP | Optional |
| World map with approximate endpoint locations | Implemented when GeoIP is configured |
| Per-destination / per-country byte totals | Not implemented |
| Torrent-client integrations | Planned |

**Measurement accuracy:** interface counters give accurate byte totals for the selected adapter, but they do not identify which remote IP received each byte. The current prototype separately discovers active remote IP/port endpoints; it does not assign interface bytes to those endpoints. Per-IP and per-country GB totals are deliberately not displayed until a trustworthy flow-accounting source is implemented.

## Resource-conscious collection

- Interface counters: sampled once per second.
- OS socket table: scanned every 15 seconds.
- Endpoint list: capped at 2,000 entries per scan.
- Traffic history: sampled every 10 seconds and retained for 90 days.
- GeoIP results: cached in memory (maximum 4,096 addresses) to avoid repeated disk lookups.
- SQLite uses WAL mode and normal synchronous mode.

These are conservative defaults, not a promise of zero CPU usage. Actual overhead depends on the number of active sockets, Windows version, and hardware.

## Requirements

- Windows 10/11 (first target)
- Python 3.11 or newer
- A local GeoIP City database in .mmdb format is optional

## Run from source

Open PowerShell in the repository folder:

    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e ".[geoip,dev]"
    python -m swarmatlas

Without GeoIP support:

    python -m pip install -e .
    python -m swarmatlas

Or run run_windows.bat after installing the dependencies.

## GeoIP setup

Choose a local MaxMind-compatible City database in Settings → GeoIP database. Obtain the database from a provider whose licence permits your use and follow that provider's terms.

IP geolocation is approximate. It generally indicates an ISP's registered or egress area, not a person's exact location.

## Local data and privacy

All application data stays on this PC:

- Windows: %APPDATA%\SwarmAtlas\
- Other platforms: ~/.local/share/SwarmAtlas/

The SQLite database stores interface counters, traffic-rate samples, and observed public endpoint metadata. It never stores packet contents. No remote geolocation requests are made. Old endpoint records are removed after 30 days; traffic-rate samples are retained for 90 days.

## Current measurement model

- **Upload / Download:** byte deltas from the selected network interface's OS counters.
- **Rate:** counter delta divided by elapsed time.
- **Destinations:** public remote IP/port endpoints visible in the local socket table.
- **Geography:** optional lookup against the local GeoIP database.
- **Per-destination GB:** unavailable until a separate flow-accounting backend is implemented.

## Roadmap

1. Validate passive collection on Windows across Ethernet, Wi-Fi, VPNs, sleep/resume, and heavy P2P workloads.
2. Add a safe, opt-in per-flow accounting backend after documenting its Windows APIs and failure modes.
3. Aggregate measured bytes by country, region, and city.
4. Add qBittorrent and other torrent-client adapters using their documented local APIs.
5. Add packaging and optional startup integration, always user-controlled.

## Development

    python -m pytest
    python -m compileall src

## License

MIT. See LICENSE.
