use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

mod geo;
mod network;
mod platform;

use platform::{
    collect_tcp_flow_stats as read_tcp_flow_stats,
    count_udp_bindings as read_udp_count,
    list_network_interfaces as read_network_interfaces,
    list_tcp_connections as read_tcp_connections,
    list_udp_bindings as read_udp_bindings,
    resolve_process_names as read_process_names,
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

/// Read TCP payload byte counters for established connections to public IPs.
#[pyfunction]
#[pyo3(signature = (limit=2000))]
fn collect_tcp_flow_stats(py: Python<'_>, limit: usize) -> PyResult<Vec<platform::TcpFlowCounter>> {
    let bounded_limit = limit.clamp(1, 100_000);
    py.detach(move || read_tcp_flow_stats(bounded_limit))
        .map_err(PyRuntimeError::new_err)
}

#[pyfunction]
fn count_udp_bindings(py: Python<'_>) -> PyResult<usize> {
    py.detach(read_udp_count).map_err(PyRuntimeError::new_err)
}

/// Return adapter aliases, operational state, sent bytes and received bytes.
#[pyfunction]
fn list_network_interfaces(py: Python<'_>) -> PyResult<Vec<platform::NetworkInterface>> {
    py.detach(read_network_interfaces)
        .map_err(PyRuntimeError::new_err)
}

/// Resolve only the process IDs present in the current socket snapshot.
#[pyfunction]
fn resolve_process_names(
    py: Python<'_>,
    process_ids: Vec<u32>,
) -> PyResult<Vec<platform::ProcessName>> {
    const MAX_PROCESS_IDS: usize = 4096;
    if process_ids.len() > MAX_PROCESS_IDS {
        return Err(PyValueError::new_err(format!(
            "at most {MAX_PROCESS_IDS} process IDs can be resolved per call"
        )));
    }
    py.detach(move || read_process_names(&process_ids))
        .map_err(PyRuntimeError::new_err)
}

/// Batch geometry work so Python crosses the FFI boundary once per bounded
/// group of rings instead of once per ring. The CPU-heavy loop runs without GIL.
#[pyfunction]
#[pyo3(signature = (rings, tolerance=0.00001))]
fn project_and_simplify_rings(
    py: Python<'_>,
    rings: Vec<Vec<(f64, f64)>>,
    tolerance: f64,
) -> PyResult<Vec<Vec<(f64, f64)>>> {
    if !tolerance.is_finite() || tolerance < 0.0 {
        return Err(PyValueError::new_err(
            "tolerance must be a finite, non-negative number",
        ));
    }

    Ok(py.detach(move || {
        rings
            .iter()
            .map(|ring| geo::geometry::project_and_simplify_ring(ring, tolerance))
            .collect()
    }))
}

#[pyfunction]
fn capabilities() -> Vec<&'static str> {
    vec![
        "windows-ip-helper",
        "tcp-owner-pid",
        "tcp-public-endpoints",
        "udp-owner-pid",
        "udp-socket-count",
        "map-geometry-v1",
        "network-interfaces-v1",
        "process-names-v1",
        "tcp-flow-estats-v1",
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
    module.add_function(wrap_pyfunction!(collect_tcp_flow_stats, module)?)?;
    module.add_function(wrap_pyfunction!(list_network_interfaces, module)?)?;
    module.add_function(wrap_pyfunction!(resolve_process_names, module)?)?;
    module.add_function(wrap_pyfunction!(project_and_simplify_rings, module)?)?;
    module.add_function(wrap_pyfunction!(capabilities, module)?)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
