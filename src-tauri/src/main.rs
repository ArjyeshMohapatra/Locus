#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{
    CustomMenuItem, Manager, RunEvent, State, SystemTray, SystemTrayEvent, SystemTrayMenu,
    SystemTrayMenuItem, WindowEvent,
};

const DEFAULT_BACKEND_PORT: u16 = 8000;
const BACKEND_PORT_SEARCH_LIMIT: u16 = 20;
const BACKEND_STARTUP_TIMEOUT_SECS: u64 = 45;
const BACKEND_POLL_INTERVAL_MS: u64 = 150;

struct BackendState {
    child: Mutex<Option<Child>>,
    port: u16,
}

fn pick_backend_port(preferred: u16) -> u16 {
    for offset in 0..=BACKEND_PORT_SEARCH_LIMIT {
        let candidate = preferred.saturating_add(offset);
        if let Ok(listener) = TcpListener::bind(("127.0.0.1", candidate)) {
            drop(listener);
            return candidate;
        }
    }
    preferred
}

fn stop_backend_process(state: &BackendState) {
    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn backend_healthcheck_once(port: u16) -> bool {
    let addr = format!("127.0.0.1:{}", port);
    let mut stream = match TcpStream::connect_timeout(
        &addr.parse().expect("invalid backend socket address"),
        Duration::from_millis(500),
    ) {
        Ok(stream) => stream,
        Err(_) => return false,
    };

    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));

    let request = b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(request).is_err() {
        return false;
    }

    let mut response = [0_u8; 256];
    match stream.read(&mut response) {
        Ok(0) => false,
        Ok(n) => {
            let text = String::from_utf8_lossy(&response[..n]);
            text.starts_with("HTTP/1.1 200") || text.starts_with("HTTP/1.0 200")
        }
        Err(_) => false,
    }
}

fn resolve_backend_bin_path() -> PathBuf {
    let current_exe = std::env::current_exe().expect("failed to resolve current executable");
    let exe_dir = current_exe
        .parent()
        .expect("current executable has no parent directory");

    let direct = exe_dir.join("locus-backend");
    if direct.exists() {
        return direct;
    }

    #[cfg(target_os = "windows")]
    {
        let windows_bin = exe_dir.join("locus-backend.exe");
        if windows_bin.exists() {
            return windows_bin;
        }
    }

    panic!(
        "failed to locate backend executable next to app binary: {}",
        direct.display()
    );
}

fn resolve_optional_window_probe_bin_path() -> Option<PathBuf> {
    let current_exe = std::env::current_exe().ok()?;
    let exe_dir = current_exe.parent()?;

    let direct = exe_dir.join("locus-window-probe");
    if direct.exists() {
        return Some(direct);
    }

    #[cfg(target_os = "windows")]
    {
        let windows_bin = exe_dir.join("locus-window-probe.exe");
        if windows_bin.exists() {
            return Some(windows_bin);
        }
    }

    None
}

fn resolve_locus_data_dir() -> PathBuf {
    if let Ok(explicit) = std::env::var("LOCUS_DATA_DIR") {
        let trimmed = explicit.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }

    if let Ok(xdg_data_home) = std::env::var("XDG_DATA_HOME") {
        let trimmed = xdg_data_home.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed).join("locus");
        }
    }

    let home = std::env::var("HOME").unwrap_or_else(|_| String::from("."));
    PathBuf::from(home).join(".local").join("share").join("locus")
}

fn wait_for_backend_ready_or_exit(
    child: &mut Child,
    port: u16,
    timeout: Duration,
) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if backend_healthcheck_once(port) {
            return Ok(());
        }

        match child.try_wait() {
            Ok(Some(status)) => {
                return Err(format!(
                    "backend exited before healthcheck on port {} with status {}",
                    port, status
                ));
            }
            Ok(None) => {}
            Err(err) => {
                return Err(format!(
                    "failed while polling backend process status: {}",
                    err
                ));
            }
        }

        thread::sleep(Duration::from_millis(BACKEND_POLL_INTERVAL_MS));
    }

    Err(format!(
        "backend failed to become ready on port {} within {}s",
        port, BACKEND_STARTUP_TIMEOUT_SECS
    ))
}

fn start_release_backend(port: u16) -> Child {
    let backend_bin = resolve_backend_bin_path();
    let data_dir = resolve_locus_data_dir();
    let _ = std::fs::create_dir_all(&data_dir);

    let mut backend_command = Command::new(&backend_bin);
    backend_command
        .env("LOCUS_PORT", port.to_string())
        .env("LOCUS_DATA_DIR", data_dir);

    if let Some(window_probe_bin) = resolve_optional_window_probe_bin_path() {
        backend_command.env("LOCUS_WINDOW_PROBE", window_probe_bin);
    }

    let mut child = backend_command
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap_or_else(|err| {
            panic!(
                "failed to spawn backend binary '{}': {}",
                backend_bin.display(),
                err
            )
        });

    if let Err(message) = wait_for_backend_ready_or_exit(
        &mut child,
        port,
        Duration::from_secs(BACKEND_STARTUP_TIMEOUT_SECS),
    ) {
        let _ = child.kill();
        let _ = child.wait();
        panic!("{}", message);
    }

    child
}

fn main() {
    let selected_port = if cfg!(debug_assertions) {
        DEFAULT_BACKEND_PORT
    } else {
        pick_backend_port(DEFAULT_BACKEND_PORT)
    };

    // In release, guarantee backend readiness before creating the UI window.
    let backend_child = if cfg!(debug_assertions) {
        None
    } else {
        Some(start_release_backend(selected_port))
    };

    let tray_menu = SystemTrayMenu::new()
        .add_item(CustomMenuItem::new("show".to_string(), "Show"))
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(CustomMenuItem::new("quit".to_string(), "Quit"));
    let system_tray = SystemTray::new().with_menu(tray_menu);

    tauri::Builder::default()
        .manage(BackendState {
            child: Mutex::new(backend_child),
            port: selected_port,
        })
        .system_tray(system_tray)
        .on_system_tray_event(|app, event| {
            if let SystemTrayEvent::MenuItemClick { id, .. } = event {
                match id.as_str() {
                    "quit" => {
                        let state: State<BackendState> = app.state();
                        stop_backend_process(&state);
                        app.exit(0);
                    }
                    "show" => {
                        if let Some(window) = app.get_window("main") {
                            window.show().unwrap();
                        }
                    }
                    _ => {}
                }
            }
        })
        .setup(|app| {
            if cfg!(debug_assertions) {
                // In dev mode, we assume the user is running the backend manually.
                println!("[tauri] Dev mode: Skipping sidecar spawn, expecting backend on port {}", DEFAULT_BACKEND_PORT);
            }

            if let Some(window) = app.get_window("main") {
                let _ = window.set_decorations(false);
            }

            Ok(())
        })
        .on_page_load(|window, _payload| {
            let state: State<BackendState> = window.state();
            let backend_url = format!("http://127.0.0.1:{}", state.port);
            let script = format!(
                "window.__LOCUS_BACKEND_URL = '{0}'; window.localStorage.setItem('locus-backend-url', '{0}');",
                backend_url
            );
            let _ = window.eval(script.as_str());
        })
        .on_window_event(|event| {
            if let WindowEvent::CloseRequested { api, .. } = event.event() {
                if let Err(err) = event.window().hide() {
                    eprintln!("[tauri] failed to hide window on close request: {}", err);
                }
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                let state: State<BackendState> = app_handle.state();
                stop_backend_process(&state);
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn pick_backend_port_uses_preferred_when_free() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("ephemeral bind failed");
        let preferred = listener
            .local_addr()
            .expect("failed to read local addr")
            .port();
        drop(listener);

        let selected = pick_backend_port(preferred);
        assert_eq!(selected, preferred);
    }

    #[test]
    fn pick_backend_port_moves_forward_when_preferred_is_busy() {
        let busy_listener =
            TcpListener::bind(("127.0.0.1", 0)).expect("failed to reserve busy port");
        let preferred = busy_listener
            .local_addr()
            .expect("failed to read busy port")
            .port();

        let selected = pick_backend_port(preferred);
        assert_ne!(selected, preferred);
        assert!(selected >= preferred);
        assert!(selected <= preferred.saturating_add(BACKEND_PORT_SEARCH_LIMIT));
    }
}
