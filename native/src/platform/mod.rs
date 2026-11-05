#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub(crate) use windows::{collect_tcp_flow_stats, count_udp_bindings, list_network_interfaces, list_tcp_connections, list_udp_bindings, resolve_process_names, NetworkInterface, ProcessName, TcpConnection, TcpFlowCounter, UdpBinding};

#[cfg(not(windows))]
mod unsupported;
#[cfg(not(windows))]
pub(crate) use unsupported::{collect_tcp_flow_stats, count_udp_bindings, list_network_interfaces, list_tcp_connections, list_udp_bindings, resolve_process_names, NetworkInterface, ProcessName, TcpConnection, TcpFlowCounter, UdpBinding};
