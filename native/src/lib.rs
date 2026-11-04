use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

mod network;
mod platform;

use platform::{
    count_udp_bindings as read_udp_count,
    list_tcp_connections as read_tcp_connections,
    list_udp_bindings as read_udp_bindings,
};

#[pyfunction]
#[pyo3(signature = (limit=2000))]
fn list_tcp_connections(
    py: Python<'_>,
    limit: usize,
) -> PyResult<Vec<platform::TcpConnection>> {
    let bounded_limit = limit.min(100_000);
    py.detach(move || read_tcp_connections(bounded_limit))
        .map_err(PyRuntimeError::new_err)
}

#[pyfunction]
#[pyo3(signature = (limit=50000))]
fn list_udp_bindings(
    py: Python<'_>,
    limit: usize,
) -> PyResult<Vec<platform::UdpBinding>> {
    let bounded_limit = limit.min(100_000);
    py.detach(move || read_udp_bindings(bounded_limit))
        .map_err(PyRuntimeError::new_err)
}

#[pyfunction]
fn count_udp_bindings(py: Python<'_>) -> PyResult<usize> {
    py.detach(read_udp_count).map_err(PyRuntimeError::new_err)
}

#[pyfunction]
fn capabilities() -> Vec<&'static str> {
    vec![
        "windows-ip-helper",
        "tcp-owner-pid",
        "tcp-public-endpoints",
        "udp-owner-pid",
        "udp-socket-count",
        "ipv4",
        "ipv6",
        "read-only",
    ]
}

#[pymodule]
fn _swarmatlas_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(list_tcp_connections, module)?)?;
    module.add_function(wrap_pyfunction!(list_udp_bindings, module)?)?;
    module.add_function(wrap_pyfunction!(count_udp_bindings, module)?)?;
    module.add_function(wrap_pyfunction!(capabilities, module)?)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
