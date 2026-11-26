use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use std::net::{IpAddr, ToSocketAddrs};
use std::time::Duration;

/// Resolve one target and send one ICMP echo request through the Rust engine.
#[pyfunction]
#[pyo3(signature = (target, timeout_ms=1200))]
fn ping_host(py: Python<'_>, target: String, timeout_ms: u64) -> PyResult<(String, Option<f64>, String)> {
    let target = target.trim().to_owned();
    if target.is_empty() || target.len() > 253 {
        return Err(PyValueError::new_err("target must be a hostname or IP address"));
    }
    if !(100..=10_000).contains(&timeout_ms) {
        return Err(PyValueError::new_err("timeout_ms must be between 100 and 10000"));
    }

    py.detach(move || {
        let address = match target.parse::<IpAddr>() {
            Ok(ip) => ip,
            Err(_) => (target.as_str(), 0).to_socket_addrs()
                .map_err(|e| format!("DNS resolution failed: {e}"))?
                .next().ok_or_else(|| "DNS returned no addresses".to_owned())?
                .ip(),
        };
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| format!("Tokio runtime failed: {e}"))?;
        let payload = [0u8; 16];
        let result = runtime.block_on(async {
            tokio::time::timeout(
                Duration::from_millis(timeout_ms),
                surge_ping::ping(address, &payload),
            ).await
        });
        match result {
            Ok(Ok((_packet, elapsed))) => Ok((address.to_string(), Some(elapsed.as_secs_f64() * 1000.0), "reply".to_owned())),
            Ok(Err(error)) => Ok((address.to_string(), None, format!("ICMP error: {error}"))),
            Err(_) => Ok((address.to_string(), None, "timeout".to_owned())),
        }
    }).map_err(PyRuntimeError::new_err)
}
