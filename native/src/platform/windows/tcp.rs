use super::api::{
    decode_port, query_tcp_rows, AF_INET, AF_INET6, MAX_TABLE_ROWS, TCP_TABLE_OWNER_PID_ALL,
};
use crate::network::address::{canonical_ip, is_public_ip};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

#[repr(C)]
#[derive(Clone, Copy)]
struct TcpRowV4 {
    state: u32,
    local_addr: u32,
    local_port: u32,
    remote_addr: u32,
    remote_port: u32,
    owning_pid: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct TcpRowV6 {
    local_addr: [u8; 16],
    local_scope_id: u32,
    local_port: u32,
    remote_addr: [u8; 16],
    remote_scope_id: u32,
    remote_port: u32,
    state: u32,
    owning_pid: u32,
}

pub(super) type TcpConnection = (String, u16, String, u32, u16, String);

fn tcp_state(state: u32) -> &'static str {
    match state {
        1 => "CLOSED",
        2 => "LISTEN",
        3 => "SYN_SENT",
        4 => "SYN_RECEIVED",
        5 => "ESTABLISHED",
        6 => "FIN_WAIT_1",
        7 => "FIN_WAIT_2",
        8 => "CLOSE_WAIT",
        9 => "CLOSING",
        10 => "LAST_ACK",
        11 => "TIME_WAIT",
        12 => "DELETE_TCB",
        _ => "UNKNOWN",
    }
}

pub(super) fn list_tcp_connections(limit: usize) -> Result<Vec<TcpConnection>, String> {
    let limit = limit.min(MAX_TABLE_ROWS);
    if limit == 0 {
        return Ok(Vec::new());
    }

    let mut rows = Vec::with_capacity(limit.min(2048));
    let mut successful_tables = 0_usize;
    let mut errors = Vec::new();

    unsafe {
        match query_tcp_rows::<TcpRowV4>(
            AF_INET,
            TCP_TABLE_OWNER_PID_ALL,
            MAX_TABLE_ROWS,
            "GetExtendedTcpTable(AF_INET)",
        ) {
            Ok(table) => {
                successful_tables += 1;
                for row in table {
                    let remote = Ipv4Addr::from(row.remote_addr.to_ne_bytes());
                    let remote = IpAddr::V4(remote);
                    if !is_public_ip(remote) {
                        continue;
                    }
                    rows.push((
                        remote.to_string(),
                        decode_port(row.remote_port),
                        "TCP".to_owned(),
                        row.owning_pid,
                        decode_port(row.local_port),
                        tcp_state(row.state).to_owned(),
                    ));
                    if rows.len() >= limit {
                        break;
                    }
                }
            }
            Err(error) => errors.push(error),
        }

        let remaining = limit.saturating_sub(rows.len());
        if remaining > 0 {
            match query_tcp_rows::<TcpRowV6>(
                AF_INET6,
                TCP_TABLE_OWNER_PID_ALL,
                MAX_TABLE_ROWS,
                "GetExtendedTcpTable(AF_INET6)",
            ) {
                Ok(table) => {
                    successful_tables += 1;
                    for row in table {
                        let remote = Ipv6Addr::from(row.remote_addr);
                        let remote = canonical_ip(IpAddr::V6(remote));
                        if !is_public_ip(remote) {
                            continue;
                        }
                        rows.push((
                            remote.to_string(),
                            decode_port(row.remote_port),
                            "TCP".to_owned(),
                            row.owning_pid,
                            decode_port(row.local_port),
                            tcp_state(row.state).to_owned(),
                        ));
                        if rows.len() >= limit {
                            break;
                        }
                    }
                }
                Err(error) => errors.push(error),
            }
        }
    }

    if successful_tables == 0 {
        return Err(errors.join("; "));
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::tcp_state;

    #[test]
    fn tcp_state_names_are_stable() {
        assert_eq!(tcp_state(5), "ESTABLISHED");
        assert_eq!(tcp_state(2), "LISTEN");
        assert_eq!(tcp_state(99), "UNKNOWN");
    }
}
