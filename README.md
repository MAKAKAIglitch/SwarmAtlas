# SwarmAtlas

**Map your share.** A lightweight, local-first desktop prototype for understanding the network traffic your PC sends and receives.

SwarmAtlas is being built around one idea: show how your computer participates in the wider network, with a clean geographic view and useful history.

## Prototype status

This is the first working foundation, not the finished geographic traffic-attribution engine.

| Feature | Prototype status |
|---|---|
| Per-interface upload/download totals | Implemented |
| Current upload/download rate | Implemented |
| Persistent local history (SQLite) | Implemented |
| Active public remote endpoints | Implemented |
| Country/city lookup from a local GeoIP database | Optional |
| World map with approximate endpoint locations | Implemented when GeoIP is configured |
| Per-destination / per-country byte totals | Not implemented yet |
| Torrent-client integrations | Planned |
| Start with Windows | Planned |

**Important accuracy note:** Windows interface counters provide accurate byte totals for the selected network adapter, but they do not say which remote IP received each byte. The current prototype separately discovers active remote endpoints and can map those endpoints geographically; it does not invent or distribute interface bytes across them. Exact per-IP and per-country GB accounting needs a flow/packet accounting backend, which is a separate next milestone.

## Design goals

- Minimal interface: map first, numbers second.
- Low idle overhead: interface counters are sampled once per second; remote sockets are refreshed every five seconds.
- Local-first: traffic history stays in a local SQLite database.
- Private by default: no IP addresses are uploaded to a geolocation service.
- Honest measurements: interface totals and observed endpoints are shown as separate kinds of data.

## Requirements

- Windows 10/11 (the first target platform)
- Python 3.11 or newer
- A local GeoIP City database in .mmdb format is optional

## Run from source

Open PowerShell in the repository folder:

    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e ".[geoip,dev]"
    python -m swarmatlas

Without GeoIP support, install with:

    python -m pip install -e .

You can also run run_windows.bat after installing the dependencies.

## GeoIP setup

SwarmAtlas can read a local MaxMind-compatible City database. Install the optional dependency with:

    pip install -e ".[geoip]"

Then choose your .mmdb file in Settings → GeoIP database.

The database is not bundled. Obtain it from a provider whose licence permits your use, and follow that provider's terms. If no database is configured, the application still counts interface traffic and lists public remote IP endpoints, but geographic fields remain unknown.

IP geolocation is approximate. It usually identifies an ISP egress area or network registration point, not a person's exact location.

## Data and privacy

The application stores its database and settings in the current user's application-data directory:

- Windows: %APPDATA%\SwarmAtlas\
- Other platforms: ~/.local/share/SwarmAtlas/

The local database stores interface totals, samples, and observed public endpoints. It does not store packet payloads. No remote geolocation lookups are made.

## Current measurement model

- **Upload / Download:** byte deltas from the selected network interface's operating-system counters.
- **Rate:** delta divided by elapsed sampling time.
- **Destinations:** public remote IP/port endpoints visible in the local socket table.
- **Geography:** optional lookup against the local GeoIP database.
- **Per-destination GB:** intentionally not displayed until a proper flow accounting backend is available.

The socket table may be incomplete when Windows restricts access to connection information. Running with elevated rights can expose more process sockets, but the application does not require elevation for interface totals.

## Roadmap

1. Validate low-overhead collection on Windows across Ethernet, Wi-Fi, VPNs, and sleep/resume.
2. Add a flow-accounting backend for per-remote-IP byte counters without storing payloads.
3. Aggregate measured bytes by country, region, and city.
4. Add qBittorrent and other torrent-client adapters for client-reported per-peer transfer totals.
5. Add Windows login startup and packaging.

## Development

    python -m pytest
    python -m compileall src

## License

MIT. See LICENSE.
