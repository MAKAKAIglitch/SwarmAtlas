#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub(crate) use windows::{count_udp_bindings, list_network_interfaces, list_tcp_connections, list_udp_bindings, resolve_process_names, NetworkInterface, ProcessName, TcpConnection, UdpBinding};

#[cfg(not(windows))]
mod unsupported;
#[cfg(not(windows))]
pub(crate) use unsupported::{count_udp_bindings, list_network_interfaces, list_tcp_connections, list_udp_bindings, resolve_process_names, NetworkInterface, ProcessName, TcpConnection, UdpBinding};
