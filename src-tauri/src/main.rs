#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main(){
    tauri::Builder::default()
        .setup(|app| {
            // Spawn the backend sidecar
            let (_rx, _child) = tauri::api::process::Command::new_sidecar("locus-backend")
                .expect("failed to create sidecar command")
                .spawn()
                .expect("failed to spawn sidecar");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}