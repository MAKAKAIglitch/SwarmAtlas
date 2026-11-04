#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub(crate) use windows::{count_udp_bindings, list_tcp_connections, list_udp_bindings, TcpConnection, UdpBinding};

#[cfg(not(windows))]
mod unsupported;
#[cfg(not(windows))]
pub(crate) use unsupported::{count_udp_bindings, list_tcp_connections, list_udp_bindings, TcpConnection, UdpBinding};
