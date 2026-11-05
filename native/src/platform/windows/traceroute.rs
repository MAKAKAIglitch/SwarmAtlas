use socket2::{Domain, Protocol, SockAddr, Socket, Type};
use std::io::{Error, ErrorKind, Result};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, ToSocketAddrs};
use std::time::{Duration, Instant};

pub(crate) type TraceProbe = (Option<String>, Option<f64>, bool, String);

const MAX_TIMEOUT_MS: u64 = 10_000;
const MIN_TIMEOUT_MS: u64 = 100;
const MAX_TTL: u32 = 64;
const UDP_BASE_PORT: u16 = 33434;

fn resolve_target(target: &str, port: u16, family: u8) -> Result<SocketAddr> {
    let target = target.trim();
    if target.is_empty() || target.len() > 253 || target.chars().any(char::is_whitespace) {
        return Err(Error::new(
            ErrorKind::InvalidInput,
            "Invalid traceroute target",
        ));
    }
    if family != 0 && family != 4 && family != 6 {
        return Err(Error::new(
            ErrorKind::InvalidInput,
            "Address family must be 0, 4, or 6",
        ));
    }
    (target, port)
        .to_socket_addrs()?
        .find(|address| {
            family == 0 || (family == 4 && address.is_ipv4()) || (family == 6 && address.is_ipv6())
        })
        .ok_or_else(|| {
            Error::new(
                ErrorKind::AddrNotAvailable,
                "Target did not resolve to the selected address family",
            )
        })
}

fn raw_socket(target: SocketAddr, timeout: Duration) -> Result<Socket> {
    let (domain, protocol) = match target {
        SocketAddr::V4(_) => (Domain::IPV4, Protocol::ICMPV4),
        SocketAddr::V6(_) => (Domain::IPV6, Protocol::ICMPV6),
    };
    let socket = Socket::new(domain, Type::RAW, Some(protocol)).map_err(permission_hint)?;
    socket.set_read_timeout(Some(timeout))?;
    Ok(socket)
}

fn permission_hint(error: Error) -> Error {
    if error.raw_os_error() == Some(10013) || error.kind() == ErrorKind::PermissionDenied {
        Error::new(
            ErrorKind::PermissionDenied,
            "Raw ICMP sockets require SwarmAtlas to run as Administrator. Start the application elevated and retry.",
        )
    } else {
        error
    }
}

fn set_hop_limit(socket: &Socket, target: SocketAddr, ttl: u32) -> Result<()> {
    match target {
        SocketAddr::V4(_) => socket.set_ttl_v4(ttl),
        SocketAddr::V6(_) => socket.set_unicast_hops_v6(ttl),
    }
}

fn checksum(bytes: &[u8]) -> u16 {
    let mut sum = 0_u32;
    let mut chunks = bytes.chunks_exact(2);
    for pair in &mut chunks {
        sum += u16::from_be_bytes([pair[0], pair[1]]) as u32;
    }
    if let Some(&last) = chunks.remainder().first() {
        sum += (last as u32) << 8;
    }
    while (sum >> 16) != 0 {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    !(sum as u16)
}

fn icmpv6_checksum(source: Ipv6Addr, destination: Ipv6Addr, packet: &[u8]) -> u16 {
    let mut pseudo_header = Vec::with_capacity(40 + 8 + packet.len());
    pseudo_header.extend_from_slice(&source.octets());
    pseudo_header.extend_from_slice(&destination.octets());
    pseudo_header.extend_from_slice(&(packet.len() as u32).to_be_bytes());
    pseudo_header.extend_from_slice(&[0, 0, 0, 58]);
    pseudo_header.extend_from_slice(packet);
    checksum(&pseudo_header)
}

fn echo_packet(target: SocketAddr, ttl: u32, source: Option<IpAddr>) -> Result<Vec<u8>> {
    let identifier = (std::process::id() & 0xffff) as u16;
    let sequence = ttl as u16;
    let mut packet = match target {
        SocketAddr::V4(_) => vec![8, 0, 0, 0],
        SocketAddr::V6(_) => vec![128, 0, 0, 0],
    };
    packet.extend_from_slice(&identifier.to_be_bytes());
    packet.extend_from_slice(&sequence.to_be_bytes());
    packet.extend_from_slice(b"SwarmAtlas");

    match target.ip() {
        IpAddr::V4(_) => {
            let sum = checksum(&packet);
            packet[2..4].copy_from_slice(&sum.to_be_bytes());
        }
        IpAddr::V6(destination) => {
            let source = source
                .and_then(|address| match address {
                    IpAddr::V6(value) => Some(value),
                    IpAddr::V4(_) => None,
                })
                .ok_or_else(|| {
                    Error::new(
                        ErrorKind::AddrNotAvailable,
                        "Could not select an IPv6 source address",
                    )
                })?;
            let sum = icmpv6_checksum(source, destination, &packet);
            packet[2..4].copy_from_slice(&sum.to_be_bytes());
        }
    }
    Ok(packet)
}

fn select_ipv6_source(target: SocketAddr) -> Result<Ipv6Addr> {
    let mut destination = target;
    destination.set_port(UDP_BASE_PORT);
    let socket = Socket::new(Domain::IPV6, Type::DGRAM, Some(Protocol::UDP))?;
    // UDP connect selects a route and local source address; it sends no datagram.
    socket.connect(&SockAddr::from(destination))?;
    match socket.local_addr()?.as_socket().map(|address| address.ip()) {
        Some(IpAddr::V6(address)) if !address.is_unspecified() => Ok(address),
        _ => Err(Error::new(
            ErrorKind::AddrNotAvailable,
            "Could not determine the IPv6 source address",
        )),
    }
}

fn icmp_offset(packet: &[u8], ipv6: bool) -> Option<usize> {
    let first = *packet.first()?;
    let offset = if ipv6 {
        if first >> 4 == 6 {
            40
        } else {
            0
        }
    } else if first >> 4 == 4 {
        let header_len = ((first & 0x0f) as usize) * 4;
        if header_len < 20 {
            return None;
        }
        header_len
    } else {
        0
    };
    (packet.len() >= offset + 2).then_some(offset)
}

fn icmp_kind(packet: &[u8], ipv6: bool) -> Option<(u8, u8)> {
    let offset = icmp_offset(packet, ipv6)?;
    Some((packet[offset], packet[offset + 1]))
}

fn icmp_reply_matches_target(packet: &[u8], ipv6: bool, source: IpAddr, target: IpAddr) -> bool {
    let Some(offset) = icmp_offset(packet, ipv6) else {
        return false;
    };
    let Some((kind, _)) = icmp_kind(packet, ipv6) else {
        return false;
    };

    if (!ipv6 && kind == 0) || (ipv6 && kind == 129) {
        return source == target;
    }

    let is_error = (!ipv6 && matches!(kind, 3 | 11)) || (ipv6 && matches!(kind, 1 | 3));
    if !is_error {
        return false;
    }

    let inner = offset + 8;
    match target {
        IpAddr::V4(expected) if !ipv6 => {
            if packet.len() < inner + 20 || packet[inner] >> 4 != 4 {
                return false;
            }
            let header_len = ((packet[inner] & 0x0f) as usize) * 4;
            if header_len < 20 || packet.len() < inner + header_len {
                return false;
            }
            let actual = Ipv4Addr::new(
                packet[inner + 16],
                packet[inner + 17],
                packet[inner + 18],
                packet[inner + 19],
            );
            actual == expected
        }
        IpAddr::V6(expected) if ipv6 => {
            if packet.len() < inner + 40 || packet[inner] >> 4 != 6 {
                return false;
            }
            let mut octets = [0_u8; 16];
            octets.copy_from_slice(&packet[inner + 24..inner + 40]);
            Ipv6Addr::from(octets) == expected
        }
        _ => false,
    }
}

fn is_relevant_icmp(packet: &[u8], ipv6: bool) -> bool {
    match icmp_kind(packet, ipv6) {
        Some((0, _)) if !ipv6 => true,
        Some((11, _)) if !ipv6 => true,
        Some((3, _)) if !ipv6 => true,
        Some((129, _)) if ipv6 => true,
        Some((3, _)) if ipv6 => true,
        Some((1, _)) if ipv6 => true,
        _ => false,
    }
}

fn is_destination_reply(packet: &[u8], ipv6: bool, source: IpAddr, target: IpAddr) -> bool {
    if source != target {
        return false;
    }
    match icmp_kind(packet, ipv6) {
        Some((0, _)) if !ipv6 => true,
        Some((129, _)) if ipv6 => true,
        // Destination unreachable from the target (e.g. UDP port unreachable)
        // proves the probe reached the destination host.
        Some((3, _)) if !ipv6 => true,
        Some((1, _)) if ipv6 => true,
        _ => false,
    }
}

fn receive_reply(
    receiver: &Socket,
    target: SocketAddr,
    timeout: Duration,
) -> Result<(Option<String>, Option<f64>, bool, String)> {
    let started = Instant::now();
    receive_reply_since(receiver, target, timeout, timeout, started)
}

fn receive_reply_since(
    receiver: &Socket,
    target: SocketAddr,
    wait_timeout: Duration,
    rtt_timeout: Duration,
    rtt_started: Instant,
) -> Result<(Option<String>, Option<f64>, bool, String)> {
    let wait_started = Instant::now();
    let ipv6 = target.is_ipv6();
    let mut buffer = vec![std::mem::MaybeUninit::<u8>::uninit(); 2048];

    loop {
        let remaining = wait_timeout.saturating_sub(wait_started.elapsed());
        if remaining.is_zero() {
            return Ok((None, None, false, "timeout".to_owned()));
        }
        receiver.set_read_timeout(Some(remaining))?;
        match receiver.recv_from(&mut buffer) {
            Ok((length, from)) => {
                let Some(source) = from.as_socket() else {
                    continue;
                };
                let bytes =
                    unsafe { std::slice::from_raw_parts(buffer.as_ptr().cast::<u8>(), length) };
                if !is_relevant_icmp(bytes, ipv6)
                    || !icmp_reply_matches_target(bytes, ipv6, source.ip(), target.ip())
                {
                    continue;
                }
                let reached = is_destination_reply(bytes, ipv6, source.ip(), target.ip());
                let elapsed = rtt_started.elapsed().as_secs_f64() * 1000.0;
                return Ok((
                    Some(source.ip().to_string()),
                    Some(elapsed),
                    reached,
                    if reached {
                        "destination".to_owned()
                    } else {
                        "replied".to_owned()
                    },
                ));
            }
            Err(error)
                if error.kind() == ErrorKind::WouldBlock || error.kind() == ErrorKind::TimedOut =>
            {
                return Ok((None, None, false, "timeout".to_owned()));
            }
            Err(error) => return Err(error),
        }
    }
}

fn udp_probe(target: SocketAddr, ttl: u32, timeout: Duration) -> Result<TraceProbe> {
    let receiver = raw_socket(target, timeout).map_err(permission_hint)?;
    let sender = Socket::new(
        if target.is_ipv4() {
            Domain::IPV4
        } else {
            Domain::IPV6
        },
        Type::DGRAM,
        Some(Protocol::UDP),
    )?;
    set_hop_limit(&sender, target, ttl)?;
    let local = if target.is_ipv4() {
        SocketAddr::new(IpAddr::V4(Ipv4Addr::UNSPECIFIED), 0)
    } else {
        SocketAddr::new(IpAddr::V6(Ipv6Addr::UNSPECIFIED), 0)
    };
    sender.bind(&SockAddr::from(local))?;
    let port = UDP_BASE_PORT.saturating_add((ttl - 1).min(1000) as u16);
    let mut destination = target;
    destination.set_port(port);
    let payload = format!("SwarmAtlas traceroute ttl={ttl}").into_bytes();
    sender.send_to(&payload, &SockAddr::from(destination))?;
    receive_reply(&receiver, target, timeout)
}

fn tcp_probe(target: SocketAddr, ttl: u32, timeout: Duration, port: u16) -> Result<TraceProbe> {
    let receiver = raw_socket(target, timeout).map_err(permission_hint)?;
    let sender = Socket::new(
        if target.is_ipv4() {
            Domain::IPV4
        } else {
            Domain::IPV6
        },
        Type::STREAM,
        Some(Protocol::TCP),
    )?;
    set_hop_limit(&sender, target, ttl)?;
    let mut destination = target;
    destination.set_port(port);
    let started = Instant::now();
    let connect_result = sender.connect_timeout(&SockAddr::from(destination), timeout);
    let connect_elapsed = started.elapsed().as_secs_f64() * 1000.0;

    if connect_result.is_ok()
        || connect_result
            .as_ref()
            .is_err_and(|error| error.kind() == ErrorKind::ConnectionRefused)
    {
        return Ok((
            Some(target.ip().to_string()),
            Some(connect_elapsed),
            true,
            "destination".to_owned(),
        ));
    }

    match receive_reply_since(
        &receiver,
        target,
        Duration::from_millis(100),
        timeout,
        started,
    ) {
        Ok((Some(address), rtt, reached, status)) => Ok((Some(address), rtt, reached, status)),
        Ok((None, _, _, _)) => Ok((None, None, false, "timeout".to_owned())),
        Err(error) => Err(error),
    }
}

fn icmp_probe(target: SocketAddr, ttl: u32, timeout: Duration) -> Result<TraceProbe> {
    let socket = raw_socket(target, timeout).map_err(permission_hint)?;
    set_hop_limit(&socket, target, ttl)?;
    let source = if target.is_ipv6() {
        Some(IpAddr::V6(select_ipv6_source(target)?))
    } else {
        None
    };
    let packet = echo_packet(target, ttl, source)?;
    socket.send_to(&packet, &SockAddr::from(target))?;
    receive_reply(&socket, target, timeout)
}

/// Perform one bounded traceroute probe for a single hop.
/// Returns (responding IP, RTT milliseconds, destination reached, status).
pub(crate) fn traceroute_probe(
    target: &str,
    method: &str,
    ttl: u32,
    timeout_ms: u64,
    port: u16,
    family: u8,
) -> std::result::Result<TraceProbe, String> {
    if !(1..=MAX_TTL).contains(&ttl) {
        return Err(format!("TTL must be between 1 and {MAX_TTL}"));
    }
    if !(MIN_TIMEOUT_MS..=MAX_TIMEOUT_MS).contains(&timeout_ms) {
        return Err(format!(
            "Timeout must be between {MIN_TIMEOUT_MS} and {MAX_TIMEOUT_MS} ms"
        ));
    }
    if port == 0 {
        return Err("TCP destination port must be between 1 and 65535".to_owned());
    }
    if !matches!(method, "icmp" | "udp" | "tcp") {
        return Err("Traceroute method must be icmp, udp, or tcp".to_owned());
    }

    let destination = resolve_target(target, port, family).map_err(|error| error.to_string())?;
    let timeout = Duration::from_millis(timeout_ms);
    let result = match method {
        "icmp" => icmp_probe(destination, ttl, timeout),
        "udp" => udp_probe(destination, ttl, timeout),
        "tcp" => tcp_probe(destination, ttl, timeout, port),
        _ => unreachable!(),
    };
    result.map_err(|error| {
        if error.raw_os_error() == Some(10013) || error.kind() == ErrorKind::PermissionDenied {
            permission_hint(error).to_string()
        } else {
            format!("Traceroute {method} probe failed: {error}")
        }
    })
}

#[cfg(test)]
mod tests {
    use super::{
        checksum, icmp_kind, icmp_reply_matches_target, icmpv6_checksum, is_destination_reply,
    };

    #[test]
    fn ipv4_echo_checksum_is_valid() {
        let packet = [8, 0, 0, 0, 0, 1, 0, 1, b'a', b'b'];
        assert_eq!(checksum(&packet), 0x969b);
    }

    #[test]
    fn parses_ipv4_icmp_after_ip_header() {
        let mut packet = vec![0x45; 20];
        packet.extend_from_slice(&[11, 0, 0, 0]);
        assert_eq!(icmp_kind(&packet, false), Some((11, 0)));
    }

    #[test]
    fn parses_ipv6_icmp_payload_without_ip_header() {
        assert_eq!(icmp_kind(&[3, 0, 0, 0], true), Some((3, 0)));
    }

    #[test]
    fn destination_unreachable_from_target_marks_udp_reached() {
        assert!(is_destination_reply(
            &[3, 3, 0, 0],
            false,
            "192.0.2.1".parse().unwrap(),
            "192.0.2.1".parse().unwrap()
        ));
    }

    #[test]
    fn ignores_time_exceeded_for_another_destination() {
        let mut packet = vec![0x45; 20];
        packet.extend_from_slice(&[11, 0, 0, 0, 0, 0, 0, 0]);
        let mut inner = vec![0x45; 20];
        inner[16..20].copy_from_slice(&[192, 0, 2, 10]);
        packet.extend_from_slice(&inner);
        assert!(!icmp_reply_matches_target(
            &packet,
            false,
            "198.51.100.1".parse().unwrap(),
            "192.0.2.99".parse().unwrap()
        ));
        assert!(icmp_reply_matches_target(
            &packet,
            false,
            "198.51.100.1".parse().unwrap(),
            "192.0.2.10".parse().unwrap()
        ));
    }

    #[test]
    fn icmpv6_checksum_includes_ipv6_pseudo_header() {
        let source: std::net::Ipv6Addr = "2001:db8::1".parse().unwrap();
        let destination: std::net::Ipv6Addr = "2001:db8::2".parse().unwrap();
        let packet = [128, 0, 0, 0, 0, 1, 0, 1];
        assert_eq!(icmpv6_checksum(source, destination, &packet), 0x2446);
    }
}

#[cfg(test)]
mod address_family_tests {
    use super::resolve_target;

    #[test]
    fn resolves_literal_addresses_for_requested_family() {
        assert!(resolve_target("127.0.0.1", 443, 4).unwrap().is_ipv4());
        assert!(resolve_target("::1", 443, 6).unwrap().is_ipv6());
        assert!(resolve_target("127.0.0.1", 443, 6).is_err());
    }
}
