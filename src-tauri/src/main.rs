#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashMap;
use std::net::TcpListener;
use std::sync::Mutex;

use tauri::{
    CustomMenuItem, Manager, RunEvent, State, SystemTray, SystemTrayEvent, SystemTrayMenu,
    SystemTrayMenuItem, WindowEvent,
};

const DEFAULT_BACKEND_PORT: u16 = 8000;
const BACKEND_PORT_SEARCH_LIMIT: u16 = 20;

struct BackendState {
    child: Mutex<Option<tauri::api::process::CommandChild>>,
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
        if let Some(child) = guard.take() {
            let _ = child.kill();
        }
    }
}

fn main() {
    let selected_port = if cfg!(debug_assertions) {
        DEFAULT_BACKEND_PORT
    } else {
        pick_backend_port(DEFAULT_BACKEND_PORT)
    };

    let tray_menu = SystemTrayMenu::new()
        .add_item(CustomMenuItem::new("show".to_string(), "Show"))
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(CustomMenuItem::new("quit".to_string(), "Quit"));
    let system_tray = SystemTray::new().with_menu(tray_menu);

    tauri::Builder::default()
        .manage(BackendState {
            child: Mutex::new(None),
            port: selected_port,
        })
        .system_tray(system_tray)
        .on_system_tray_event(|app, event| match event {
            SystemTrayEvent::MenuItemClick { id, .. } => match id.as_str() {
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
            },
            _ => { }
        })
        .setup(|app| {
            if cfg!(debug_assertions) {
                // In dev mode, we assume the user is running the backend manually.
                println!("[tauri] Dev mode: Skipping sidecar spawn, expecting backend on port {}", DEFAULT_BACKEND_PORT);
            } else {
                let state: State<BackendState> = app.state();
                let mut env_map = HashMap::new();
                env_map.insert("LOCUS_PORT".to_string(), state.port.to_string());
                
                let (_rx, child) = tauri::api::process::Command::new_sidecar("locus-backend")
                    .expect("failed to create sidecar command")
                    .envs(env_map)
                    .spawn()
                    .expect("failed to spawn sidecar");

                if let Ok(mut guard) = state.child.lock() {
                    *guard = Some(child);
                }

                if state.port != DEFAULT_BACKEND_PORT {
                    println!(
                        "[tauri] Backend port {} busy, started sidecar on {}",
                        DEFAULT_BACKEND_PORT, state.port
                    );
                }
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
        .on_window_event(|event| match event.event() {
            WindowEvent::CloseRequested { api, .. } => {
                event.window().hide().unwrap();
                api.prevent_close();
            }
            _ => {}
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