# SwarmAtlas

**Network traffic atlas.** A lightweight, local-first Windows desktop monitor that shows whole-adapter traffic rates, observed public network endpoints, their approximate geography, and long-term traffic history. It monitors ordinary application traffic, not only peer-to-peer connections.

## What it measures

SwarmAtlas combines two different Windows data sources:

- **Interface counters:** exact upload/download byte deltas and live rates for the selected network adapter. These include all traffic visible to that adapter.
- **OS connection tables:** public TCP connections and connected UDP endpoints visible to Windows, with process ownership where permitted. The map and country/region summaries are built from these endpoints.

The dashboard shows upload and download independently, updates the live rates every second, and saves the traffic history locally.

**Important accuracy boundary:** Windows interface counters do not say which remote IP received each byte. The connection table is a snapshot of sockets, not a per-flow byte counter. SwarmAtlas therefore does not invent per-IP, per-country, or per-process GB values. The world map shows observed endpoints and approximate IP geolocation—not a heatmap of measured bandwidth. All kinds of applications are in scope; P2P is only one possible source of connections.

## Read-only safety model

The default monitor only reads OS counters, socket tables and process metadata. It does not:

- install kernel drivers, NDIS filters, packet-capture drivers, services, scheduled tasks, or startup entries;
- install or configure WinDivert, Npcap, WinPcap, WFP filters/callouts, or firewall rules;
- change IP addresses, routes, DNS, proxy settings, adapter configuration, or Windows network services;
- open listening ports, create network tunnels, or send traffic on behalf of other applications;
- capture, inspect, or save packet payloads;
- require administrator privileges for interface counters;
- upload telemetry or traffic statistics to SwarmAtlas servers.

The Rust core is an ordinary user-mode Python extension using Windows IP Helper APIs. It is **not** a Windows kernel driver. If it cannot be installed or loaded, the Python/psutil collector remains available. Restricted socket-table access may make endpoint discovery incomplete; interface counters continue to work.

## GeoIP sources

Open **GeoIP provider** in the sidebar to choose one of the following. The default is DB-IP Lite offline mode. The five online choices are opt-in; when one is selected, the public destination IP being looked up is sent to that provider. The provider also sees the user's public source IP as with any online request (and the account token if required). Process names, PIDs, traffic totals and packet contents are not sent. Provider API keys are saved in the local settings file and are not encrypted by SwarmAtlas.

| Source | Mode and useful data | Important terms / limits |
|---|---|---|
| **DB-IP Lite** | Offline City MMDB: country, first-level region, city and approximate coordinates | Default and most private option. CC BY 4.0; attribution is shown in the map. |
| **IP-API.com** | Online country, region, city, coordinates, ISP and ASN/organization | Free endpoint is non-commercial only, HTTP (not HTTPS), and limited to 45 requests/minute. |
| **ipdata.co** | Online country, region, city, coordinates and ASN/organization | Free allowance: up to 1,000 city-level lookups/day. Attribution is required; commercial use is permitted under the provider's free-plan conditions. |
| **ipapi.is** | Online country, region, city, coordinates and network/ASN details | Anonymous tier: 30 lookups/day; a free account/key raises the documented allowance to 1,000/day. |
| **IPinfo Lite** | Online country and ASN/AS name | Requires an IPinfo token. Lite is country/ASN-level data; it does not provide city coordinates. Public use requires attribution. |
| **2ip.io** | Online country, city and network/ASN data | Requires an API token. The provider advertises 10,000 free requests/month after registration and a free-token rate limit of 3 requests/second. SwarmAtlas stops at 9,500 requests/month to leave a safety margin. |

The online providers are intentionally not queried in parallel: select one source at a time. Lookups run in a background worker, public IPs are cached in memory, provider request rates are limited, and daily and monthly free-tier budgets are tracked locally. Private, loopback and link-local addresses are never sent to a remote GeoIP API. Switching back to DB-IP Lite returns to local lookups.

**Privacy note:** online GeoIP necessarily discloses the looked-up destination IP to the selected provider. IP-API's free endpoint is unencrypted HTTP, so it is not a privacy-equivalent alternative to the HTTPS providers. Do not select it unless its non-commercial terms and transport characteristics suit your use. DB-IP Lite is recommended for private/offline use.

On first launch, DB-IP City Lite is downloaded from the official DB-IP host when offline mode is selected and no valid local copy exists. The file is checked against the publisher's checksum and validated as an MMDB before being installed. A valid cached file remains usable if a later refresh fails. The database refreshes after 30 days. Natural Earth country and Admin-1 boundaries are cached locally; after setup, the map can render offline.

You can also add local City, Country, ASN or ISP MMDB files using **Local MMDB files…**. The default DB-IP City Lite and the Natural Earth map files are stored in the standard application-data directory.

## Map and regions

- Interactive world map: wheel zoom, drag-to-pan, zoom controls and enlarged view.
- Country and first-level region boundaries from Natural Earth.
- Approximate IP markers from the chosen GeoIP source.
- Country and region tables count distinct observed endpoint IPs. These are **endpoint counts, not bandwidth totals**.
- IP locations are estimates and do not identify a device's physical location.

## History and database storage

The **History** chart offers 1 hour, 24 hours, 7 days, 30 days, 1 year and 5 years.

To keep long retention compact:

- traffic is sampled every 10 seconds;
- individual samples are kept for the most recent 7 days;
- older samples are folded into hourly average/peak summaries for up to 5 years;
- old endpoint records are retained for up to 5 years;
- endpoint history is deduplicated by IP, port, protocol and process instead of treating every temporary local source port as a new destination;
- endpoint history is capped at 200,000 rows;
- SQLite uses WAL mode, a bounded page cache, and checkpoint/optimization when the monitor closes.

The database file is named swarmatlas.sqlite3. Use **Database location…** in the sidebar to select another folder. SwarmAtlas stops the collector cleanly, checkpoints the SQLite WAL, copies the current database if the destination does not already contain one, then reopens it from the selected folder. Settings and diagnostic logs remain in the normal application-data directories.

Windows default application data: %APPDATA%\SwarmAtlas\. The launcher logs are in %LOCALAPPDATA%\SwarmAtlas\logs\. Logs are local; the latest 10 launcher logs are retained.

SQLite history, peer IPs, process names and diagnostic logs are not encrypted at rest by SwarmAtlas. They rely on the file permissions of the Windows user account. API tokens for online GeoIP services are also stored in the local settings file.

## Install and run on Windows

Requirements: Windows 10/11, Python 3.11 or newer, and the Python Launcher (py). Double-click run_windows.bat.

On first launch, the launcher:

1. creates the local Python virtual environment;
2. installs SwarmAtlas and its GeoIP/MMDB dependencies;
3. checks GitHub Releases for the tested Windows x64 Rust wheel;
4. downloads and verifies the wheel's SHA-256 manifest, then installs it into the virtual environment;
5. starts the application.

The Rust wheel is prebuilt, so a Rust compiler is not needed. The wheel is published by GitHub Actions after the Windows Rust API smoke test, Python tests and compile checks pass. If a tested release is not available, GitHub is unreachable, or checksum/installation verification fails, the launcher logs a warning and starts in Python compatibility mode instead. It does not install a kernel driver or change Windows networking. Later launches reuse the virtual environment and skip the native download when the extension is already installed.

An internet connection is required for the initial Python package setup and first-run reference-data downloads. The launcher keeps its console visible and mirrors setup output, application output, warnings and Python errors to a local log.

For a manual setup from PowerShell:

    py -3 -m venv .venv
    .\.venv\Scripts\python -m pip install -e ".[geoip]"
    .\.venv\Scripts\python -m swarmatlas

The manual command does not run the Windows launcher or its automatic native-wheel setup. For development dependencies:

    .\.venv\Scripts\python -m pip install -e ".[geoip,dev]"
    python -m pytest
    python -m compileall run_windows.py update_project.py src

## Optional local Rust build

The Python application, UI, localization, GeoIP orchestration, settings and SQLite store remain Python-side. The optional PyO3/Rust extension reads Windows TCP IPv4/IPv6 connection-owner tables and UDP IPv4/IPv6 local socket-owner tables through read-only Windows IP Helper APIs.

The native reader is bounded to at most 50,000 rows per address-family call; the app keeps at most 2,000 public endpoints per scan. Windows' UDP ownership table describes local socket bindings; it does not reveal every remote UDP destination. SwarmAtlas only associates a UDP peer when the OS socket table exposes a connected remote endpoint.

The prebuilt Rust extension is installed automatically by run_windows.bat when a verified release is available. To build locally instead, install Rust 1.85 or newer and the Windows MSVC linker/SDK, then run build_native.bat. The Python collector remains the fallback.

## Appearance, language and interface

- Theme: System, Dark or Light.
- Language: System, Русский or English.
- Network adapter: automatic selection or a specific active interface.
- The live header shows upload and download rates separately.
- Native Windows 11 Mica backdrop is supported on Windows 11 22H2+.

## Updating

Close SwarmAtlas and run update_project.bat.

- Git clones are updated only when the working tree is clean and a fast-forward update is possible.
- ZIP-extracted folders are safely overlaid from the GitHub main-branch archive.
- The updater preserves the Python virtual environment, local MMDB files, SQLite databases, logs and files outside the repository.
- If a Rust extension is already installed, the updater checks for a refreshed tested wheel or local release build and verifies its SHA-256 before replacing it.
- The updater does not silently stash, reset or rewrite local Git commits.

## Development and license

    python -m pytest
    python -m compileall run_windows.py update_project.py src benchmarks

SwarmAtlas application code is MIT-licensed. Third-party datasets and providers retain their own licenses and terms; see their linked terms before using an online service or redistributing downloaded GeoIP data.
