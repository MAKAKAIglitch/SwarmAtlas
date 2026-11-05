use super::api::{
    decode_port, query_tcp_rows_with, AF_INET, AF_INET6, MAX_TABLE_ROWS,
};
use crate::network::address::{canonical_ip, is_public_ip};
use std::collections::HashMap;
use std::mem::size_of;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

const TCP_CONNECTION_ESTATS_DATA: u32 = 1;
const ENABLED_CACHE_TTL: Duration = Duration::from_secs(30);

#[repr(C)]
#[derive(Clone, Copy)]
struct TcpOwnerRowV4 {
    state: u32,
    local_addr: u32,
    local_port: u32,
    remote_addr: u32,
    remote_port: u32,
    owning_pid: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct TcpOwnerRowV6 {
    local_addr: [u8; 16],
    local_scope_id: u32,
    local_port: u32,
    remote_addr: [u8; 16],
    remote_scope_id: u32,
    remote_port: u32,
    state: u32,
    owning_pid: u32,
}

#[repr(C)]
struct MibTcpRowV4 {
    state: u32,
    local_addr: u32,
    local_port: u32,
    remote_addr: u32,
    remote_port: u32,
}

#[repr(C)]
struct MibTcpRowV6 {
    local_addr: [u8; 16],
    local_scope_id: u32,
    local_port: u32,
    remote_addr: [u8; 16],
    remote_scope_id: u32,
    remote_port: u32,
    state: u32,
}

#[repr(C)]
struct TcpEstatsDataRwV0 {
    enable_collection: u8,
}

#[repr(C)]
#[derive(Default)]
struct TcpEstatsDataRodV0 {
    data_bytes_out: u64,
    data_segs_out: u64,
    data_bytes_in: u64,
    data_segs_in: u64,
    segs_out: u64,
    segs_in: u64,
    soft_errors: u32,
    soft_error_reason: u32,
    snd_una: u32,
    snd_nxt: u32,
    snd_max: u32,
    thru_bytes_acked: u64,
    rcv_nxt: u32,
    thru_bytes_received: u64,
}

pub(crate) type TcpFlowCounter = (String, u16, u32, u16, u64, u64);

#[link(name = "iphlpapi")]
unsafe extern "system" {
    fn SetPerTcpConnectionEStats(
        row: *mut MibTcpRowV4, estats_type: u32, rw: *mut u8,
        rw_version: u32, rw_size: u32, offset: u32,
    ) -> u32;
    fn GetPerTcpConnectionEStats(
        row: *mut MibTcpRowV4, estats_type: u32,
        rw: *mut u8, rw_version: u32, rw_size: u32,
        ros: *mut u8, ros_version: u32, ros_size: u32,
        rod: *mut u8, rod_version: u32, rod_size: u32,
    ) -> u32;
    fn SetPerTcp6ConnectionEStats(
        row: *mut MibTcpRowV6, estats_type: u32, rw: *mut u8,
        rw_version: u32, rw_size: u32, offset: u32,
    ) -> u32;
    fn GetPerTcp6ConnectionEStats(
        row: *mut MibTcpRowV6, estats_type: u32,
        rw: *mut u8, rw_version: u32, rw_size: u32,
        ros: *mut u8, ros_version: u32, ros_size: u32,
        rod: *mut u8, rod_version: u32, rod_size: u32,
    ) -> u32;
}

static ENABLED_FLOWS: OnceLock<Mutex<HashMap<String, Instant>>> = OnceLock::new();

fn enabled_flows() -> &'static Mutex<HashMap<String, Instant>> {
    ENABLED_FLOWS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn windows_error(api: &str, code: u32) -> String {
    if code == 5 {
        format!(
            "{api} was denied (Windows error 5). TCP flow statistics require SwarmAtlas \
             to run elevated as an Administrator."
        )
    } else {
        format!("{api} failed with Windows error {code}")
    }
}

unsafe fn enable_v4(row: &mut MibTcpRowV4) -> Result<(), String> {
    let mut rw = TcpEstatsDataRwV0 { enable_collection: 1 };
    let status = SetPerTcpConnectionEStats(
        row, TCP_CONNECTION_ESTATS_DATA,
        (&mut rw as *mut TcpEstatsDataRwV0).cast::<u8>(),
        0, size_of::<TcpEstatsDataRwV0>() as u32, 0,
    );
    if status == 0 { Ok(()) } else { Err(windows_error("SetPerTcpConnectionEStats", status)) }
}

unsafe fn read_v4(row: &mut MibTcpRowV4) -> Result<TcpEstatsDataRodV0, String> {
    let mut rod = TcpEstatsDataRodV0::default();
    let status = GetPerTcpConnectionEStats(
        row, TCP_CONNECTION_ESTATS_DATA,
        std::ptr::null_mut(), 0, 0,
        std::ptr::null_mut(), 0, 0,
        (&mut rod as *mut TcpEstatsDataRodV0).cast::<u8>(),
        0, size_of::<TcpEstatsDataRodV0>() as u32,
    );
    if status == 0 { Ok(rod) } else { Err(windows_error("GetPerTcpConnectionEStats", status)) }
}

unsafe fn enable_v6(row: &mut MibTcpRowV6) -> Result<(), String> {
    let mut rw = TcpEstatsDataRwV0 { enable_collection: 1 };
    let status = SetPerTcp6ConnectionEStats(
        row, TCP_CONNECTION_ESTATS_DATA,
        (&mut rw as *mut TcpEstatsDataRwV0).cast::<u8>(),
        0, size_of::<TcpEstatsDataRwV0>() as u32, 0,
    );
    if status == 0 { Ok(()) } else { Err(windows_error("SetPerTcp6ConnectionEStats", status)) }
}

unsafe fn read_v6(row: &mut MibTcpRowV6) -> Result<TcpEstatsDataRodV0, String> {
    let mut rod = TcpEstatsDataRodV0::default();
    let status = GetPerTcp6ConnectionEStats(
        row, TCP_CONNECTION_ESTATS_DATA,
        std::ptr::null_mut(), 0, 0,
        std::ptr::null_mut(), 0, 0,
        (&mut rod as *mut TcpEstatsDataRodV0).cast::<u8>(),
        0, size_of::<TcpEstatsDataRodV0>() as u32,
    );
    if status == 0 { Ok(rod) } else { Err(windows_error("GetPerTcp6ConnectionEStats", status)) }
}

/// Read TCP payload byte counters for established connections to public IPs.
pub(crate) fn collect_tcp_flow_stats(limit: usize) -> Result<Vec<TcpFlowCounter>, String> {
    let limit = limit.clamp(1, MAX_TABLE_ROWS);
    let now = Instant::now();
    let mut enabled = enabled_flows()
        .lock()
        .map_err(|_| "TCP ESTATS cache lock was poisoned".to_owned())?;
    enabled.retain(|_, last_seen| now.duration_since(*last_seen) < ENABLED_CACHE_TTL);

    let mut result = Vec::with_capacity(limit.min(2048));
    let mut first_error: Option<String> = None;
    let mut successful_tables = 0_usize;

    unsafe {
        match query_tcp_rows_with::<TcpOwnerRowV4, _>(
            AF_INET, super::api::TCP_TABLE_OWNER_PID_ALL, MAX_TABLE_ROWS,
            "GetExtendedTcpTable(ESTATS IPv4)",
            |table| {
                for source in table {
                    if result.len() >= limit { break; }
                    if source.state != 5 { continue; }
                    let remote = IpAddr::V4(Ipv4Addr::from(source.remote_addr.to_ne_bytes()));
                    if !is_public_ip(remote) { continue; }
                    let local = IpAddr::V4(Ipv4Addr::from(source.local_addr.to_ne_bytes()));
                    let remote_port = decode_port(source.remote_port);
                    let local_port = decode_port(source.local_port);
                    let key = format!("4:{local}:{local_port}:{remote}:{remote_port}");
                    let mut row = MibTcpRowV4 {
                        state: source.state,
                        local_addr: source.local_addr,
                        local_port: source.local_port,
                        remote_addr: source.remote_addr,
                        remote_port: source.remote_port,
                    };
                    if !enabled.contains_key(&key) {
                        match enable_v4(&mut row) {
                            Ok(()) => { enabled.insert(key.clone(), now); }
                            Err(error) => { first_error.get_or_insert(error); continue; }
                        }
                    } else {
                        enabled.insert(key.clone(), now);
                    }
                    match read_v4(&mut row) {
                        Ok(stats) => result.push((
                            remote.to_string(), remote_port, source.owning_pid, local_port,
                            stats.data_bytes_out, stats.data_bytes_in,
                        )),
                        Err(error) => {
                            enabled.remove(&key);
                            first_error.get_or_insert(error);
                        }
                    }
                }
            },
        ) {
            Ok(()) => successful_tables += 1,
            Err(error) => { first_error.get_or_insert(error); }
        }

        match query_tcp_rows_with::<TcpOwnerRowV6, _>(
            AF_INET6, super::api::TCP_TABLE_OWNER_PID_ALL, MAX_TABLE_ROWS,
            "GetExtendedTcpTable(ESTATS IPv6)",
            |table| {
                for source in table {
                    if result.len() >= limit { break; }
                    if source.state != 5 { continue; }
                    let remote = canonical_ip(IpAddr::V6(Ipv6Addr::from(source.remote_addr)));
                    if !is_public_ip(remote) { continue; }
                    let local = canonical_ip(IpAddr::V6(Ipv6Addr::from(source.local_addr)));
                    let remote_port = decode_port(source.remote_port);
                    let local_port = decode_port(source.local_port);
                    let key = format!("6:{local}:{local_port}:{remote}:{remote_port}");
                    let mut row = MibTcpRowV6 {
                        local_addr: source.local_addr,
                        local_scope_id: source.local_scope_id,
                        local_port: source.local_port,
                        remote_addr: source.remote_addr,
                        remote_scope_id: source.remote_scope_id,
                        remote_port: source.remote_port,
                        state: source.state,
                    };
                    if !enabled.contains_key(&key) {
                        match enable_v6(&mut row) {
                            Ok(()) => { enabled.insert(key.clone(), now); }
                            Err(error) => { first_error.get_or_insert(error); continue; }
                        }
                    } else {
                        enabled.insert(key.clone(), now);
                    }
                    match read_v6(&mut row) {
                        Ok(stats) => result.push((
                            remote.to_string(), remote_port, source.owning_pid, local_port,
                            stats.data_bytes_out, stats.data_bytes_in,
                        )),
                        Err(error) => {
                            enabled.remove(&key);
                            first_error.get_or_insert(error);
                        }
                    }
                }
            },
        ) {
            Ok(()) => successful_tables += 1,
            Err(error) => { first_error.get_or_insert(error); }
        }
    }

    if successful_tables == 0 {
        return Err(first_error.unwrap_or_else(|| "TCP connection tables unavailable".to_owned()));
    }
    if result.is_empty() {
        if let Some(error) = first_error { return Err(error); }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::TcpEstatsDataRodV0;
    use std::mem::size_of;

    #[test]
    fn windows_tcp_estats_data_structure_has_expected_abi_size() {
        assert_eq!(size_of::<TcpEstatsDataRodV0>(), 96);
    }
}
