# Security and safety policy

## Default behavior

SwarmAtlas is a passive, read-only network monitor. Its default GeoIP source is the local DB-IP Lite MMDB.

Allowed operations:
- Read network-interface counters and interface state through psutil.
- Read OS TCP/UDP connection-owner tables through the optional Rust extension (Windows IP Helper APIs), or through the Python psutil fallback.
- Read process IDs and, when permitted, process names for observed connections.
- Read local GeoIP MMDB files selected by the user.
- Download the public DB-IP Lite City database and Natural Earth map-boundary datasets during first-run setup or scheduled DB-IP refresh. These reference-data downloads do not contain observed peer IPs, process names, PIDs or traffic statistics.
- Request OpenStreetMap standard raster tiles for the tiles visible in the current map viewport, using the application's own User-Agent. Tile requests contain tile coordinates, not observed endpoint IPs or traffic totals. Separate Qt disk caches for the dashboard and expanded map follow HTTP cache headers and are each limited to 128 MiB.
- If the user explicitly selects an online GeoIP provider, send the public destination IP being looked up to that selected provider. No process names, PIDs, traffic totals or packet contents are included in the lookup.
- Install the project's declared Python dependencies and, when available, a verified prebuilt Rust wheel from the project's public GitHub release during Windows first-run setup.
- Write local settings, provider quota counters, traffic history, endpoint metadata and diagnostics to local application-data/storage directories.

Operations out of scope for the default monitor:
- Installing or loading kernel drivers, services, scheduled tasks, or startup hooks.
- Installing Npcap, WinPcap, WinDivert, NDIS filters, or packet-capture components.
- Adding/removing Windows Filtering Platform filters or firewall rules.
- Editing routes, DNS, proxy, IP addresses, adapter settings, or Winsock configuration.
- Capturing packet payloads or transmitting traffic on behalf of other applications.
- Automatically requesting elevation or bypassing access restrictions.

The Rust extension is a user-mode Python module, not a Windows kernel driver. It uses read-only Windows IP Helper APIs.

## Privileges

The app should run as a normal Windows user. It must not request administrator privileges. If Windows denies access to some socket metadata, show an informative status and continue interface-level monitoring.

## Network behavior and GeoIP privacy

The default DB-IP Lite mode performs local address lookups after its static MMDB file has been downloaded. The observed destination IPs are not sent to DB-IP or Natural Earth for those lookups.

The base map requests standard tiles from tile.openstreetmap.org only for the area currently visible. The OSM tile server sees the user's public source IP and the requested tile coordinates (which reveal the broad map area being viewed). These requests do not contain the application's observed destination IP list, process metadata, or traffic counters. Tile attribution is displayed on the map. The app does not prefetch or bulk-download tiles; it follows the [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

Online GeoIP is opt-in and sends the selected public destination IP to exactly one configured provider. The provider also sees the user's public source IP as the normal source of the HTTPS/HTTP request (and the account token when required):
- IP-API.com (free endpoint: HTTP, non-commercial use only);
- ipdata.co;
- ipapi.is;
- IPinfo Lite (requires a token);
- IP2Location.io (keyless or optional API token; attribution is required on its free plan);
- 2ip.io (requires a token).

The provider terms, quotas, and transport security differ. IP-API's free endpoint is HTTP and does not provide HTTPS transport. Users should check the provider's current terms before selecting a service. Private, loopback and link-local addresses are filtered out before remote lookups. Lookups run in a background thread with an in-memory cache and provider-specific request throttling. The application keeps conservative daily and monthly request counters locally per installation for free-tier budget protection. Other applications or devices sharing the same public IP or API key may also consume the provider's quota.

Online provider API keys are stored separately for each provider in the local settings file. They are not encrypted by SwarmAtlas. A token for one provider is not reused for another. A user who can read that settings file can read the key. Do not publish settings.json or application logs.

The monitor itself does not bind/listen on a port, create tunnels, change firewall settings, or capture payloads. Package installation and updater operations contact their configured package/GitHub sources only when setup or update is explicitly run. The Windows launcher downloads a published Rust wheel only from the project's GitHub release, validates the SHA-256 against the release manifest, then installs the local wheel. If that fails, it leaves the Python fallback available.

## Local data and retention

The SQLite database stores interface totals, raw traffic samples, compact hourly history, observed public endpoints, approximate GeoIP metadata, and process ownership metadata. It does not store packet payloads. Database files may be stored in the default application-data directory or in a user-selected folder.

Raw traffic samples are retained for 7 days. Older samples are compacted into hourly aggregates and retained for up to 5 years. Destination records are retained for up to 5 years, with a cap of 200,000 records. The app checkpoints and optimizes SQLite when the monitor exits.

Observed public IPs, process IDs/names, settings, API tokens, GeoIP metadata and traffic history are not uploaded to a SwarmAtlas service. However, an online GeoIP provider receives the destination IP it is asked to look up.

SQLite history, diagnostic logs, settings and GeoIP API tokens are not encrypted at rest by SwarmAtlas. They rely on the current Windows user's file permissions. Anyone with read access to those files may inspect the contents. Do not publish the database, settings file or logs publicly.

## Measurement limits

Interface counters account for all bytes reported by the selected adapter. The Windows socket table separately exposes observed public TCP connections and some connected UDP endpoints. These sources do not provide reliable per-destination byte attribution. SwarmAtlas must not label endpoint counts as bandwidth or invent per-IP/per-country transfer volumes.

## Future features

Per-flow byte accounting and packet capture are not implemented. Before selecting a backend, evaluate its privilege requirements, whether it installs filters/drivers, whether it can affect packet processing, its CPU/memory overhead, and how it is removed. Any such feature must be a separate, disabled-by-default module with explicit user consent and a documented rollback path. The read-only interface-counter mode must remain available independently.

## Reporting

Please report security or safety issues through GitHub Issues without posting private IPs, peer lists, API tokens, settings files or GeoIP database files.
