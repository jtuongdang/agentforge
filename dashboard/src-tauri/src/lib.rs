use std::{
    env,
    fs::{self, File, OpenOptions},
    io,
    path::PathBuf,
    sync::Mutex,
    thread,
    time::Duration,
};

use tauri::{Emitter, Manager, State, WindowEvent};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::{
    ShellExt,
    process::{CommandChild, CommandEvent},
};

#[derive(Default)]
struct BackendState {
    url: Mutex<Option<String>>,
    child: Mutex<Option<CommandChild>>,
}

#[tauri::command]
fn backend_url(state: State<'_, BackendState>) -> Result<String, String> {
    state
        .url
        .lock()
        .map_err(|_| "Backend state is unavailable".to_string())?
        .clone()
        .ok_or_else(|| "Backend is still starting".to_string())
}

#[tauri::command]
async fn save_checkpoint(
    app: tauri::AppHandle,
    file_name: String,
) -> Result<Option<String>, String> {
    let Some(stem) = file_name.strip_suffix(".devagent") else {
        return Err("Invalid checkpoint name".to_string());
    };
    if stem.len() != 32 || !stem.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("Invalid checkpoint name".to_string());
    }
    let checkpoint_dir = app
        .path()
        .app_data_dir()
        .map_err(|err| err.to_string())?
        .join("checkpoints");
    let directory = checkpoint_dir
        .canonicalize()
        .map_err(|err| err.to_string())?;
    let source = directory.join(&file_name);
    if source.parent() != Some(directory.as_path()) || !source.is_file() || source.is_symlink() {
        return Err("Checkpoint file is missing".to_string());
    }
    let Some(selected) = app
        .dialog()
        .file()
        .set_file_name(&file_name)
        .add_filter("DevAgent checkpoint", &["devagent"])
        .blocking_save_file()
    else {
        return Ok(None);
    };
    let mut destination = selected.into_path().map_err(|err| err.to_string())?;
    if destination.extension().and_then(|ext| ext.to_str()) != Some("devagent") {
        destination.set_extension("devagent");
    }
    if destination == source {
        return Err("Choose a destination outside the Orchestrator data directory".to_string());
    }
    let mut source_file = File::open(&source).map_err(|err| err.to_string())?;
    let mut target_file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&destination)
        .map_err(|err| {
            if err.kind() == io::ErrorKind::AlreadyExists {
                "A file already exists at that location. Choose a new name.".to_string()
            } else {
                err.to_string()
            }
        })?;
    if let Err(err) =
        io::copy(&mut source_file, &mut target_file).and_then(|_| target_file.sync_all())
    {
        let _ = fs::remove_file(&destination);
        return Err(err.to_string());
    }
    Ok(Some(destination.to_string_lossy().to_string()))
}

#[tauri::command]
async fn choose_checkpoint_restore_paths(
    app: tauri::AppHandle,
) -> Result<Option<(String, String)>, String> {
    let Some(checkpoint) = app
        .dialog()
        .file()
        .add_filter("DevAgent checkpoint", &["devagent"])
        .blocking_pick_file()
    else {
        return Ok(None);
    };
    let Some(parent) = app.dialog().file().blocking_pick_folder() else {
        return Ok(None);
    };
    let checkpoint = checkpoint.into_path().map_err(|err| err.to_string())?;
    let parent = parent.into_path().map_err(|err| err.to_string())?;
    Ok(Some((
        checkpoint.to_string_lossy().to_string(),
        parent.to_string_lossy().to_string(),
    )))
}

fn start_backend(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let inherited_path = env::var("PATH").unwrap_or_default();
    let home = env::var("HOME").unwrap_or_default();
    let desktop_path =
        format!("{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:{inherited_path}");
    let data_dir = app.path().app_data_dir()?;
    std::fs::create_dir_all(&data_dir)?;

    let command = if cfg!(debug_assertions) {
        let entry =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend/desktop_entry.py");
        app.shell()
            .command("python3")
            .args([entry.to_string_lossy().to_string()])
    } else {
        app.shell().sidecar("devagent-backend")?
    };
    let (mut events, child) = command
        .env("PATH", desktop_path)
        .env(
            "CLINIC_ORCHESTRATOR_DATA_DIR",
            data_dir.to_string_lossy().to_string(),
        )
        .env("CLINIC_ORCHESTRATOR_HOST", "127.0.0.1")
        .env("CLINIC_ORCHESTRATOR_HEADLESS", "1")
        .spawn()?;
    app.state::<BackendState>()
        .child
        .lock()
        .expect("backend lock poisoned")
        .replace(child);

    let handle = app.handle().clone();
    tauri::async_runtime::spawn(async move {
        let mut partial = String::new();
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    partial.push_str(&String::from_utf8_lossy(&bytes));
                    while let Some(newline) = partial.find('\n') {
                        let line = partial.drain(..=newline).collect::<String>();
                        if let Some(port) = line
                            .trim()
                            .strip_prefix("READY:")
                            .and_then(|value| value.parse::<u16>().ok())
                        {
                            let url = format!("http://127.0.0.1:{port}");
                            if let Some(state) = handle.try_state::<BackendState>() {
                                if let Ok(mut current) = state.url.lock() {
                                    *current = Some(url.clone());
                                }
                            }
                            let _ = handle.emit("backend-ready", url);
                        }
                    }
                }
                CommandEvent::Terminated(_) => {
                    if let Some(state) = handle.try_state::<BackendState>() {
                        if let Ok(mut current) = state.url.lock() {
                            *current = None;
                        }
                    }
                    let _ = handle.emit("backend-stopped", ());
                }
                CommandEvent::Error(error) => {
                    let _ = handle.emit("backend-error", error);
                }
                _ => {}
            }
        }
    });
    Ok(())
}

fn stop_backend(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<BackendState>() else {
        return;
    };
    let Ok(mut child_guard) = state.child.lock() else {
        return;
    };
    let Some(child) = child_guard.take() else {
        return;
    };
    #[cfg(unix)]
    unsafe {
        libc::kill(child.pid() as i32, libc::SIGTERM);
    }
    #[cfg(not(unix))]
    let _ = child.kill();

    #[cfg(unix)]
    {
        for _ in 0..80 {
            let alive = unsafe { libc::kill(child.pid() as i32, 0) == 0 };
            if !alive {
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = child.kill();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .manage(BackendState::default())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            backend_url,
            save_checkpoint,
            choose_checkpoint_restore_paths
        ])
        .setup(|app| start_backend(app))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                window.app_handle().exit(0);
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Tauri application");

    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            stop_backend(handle);
        }
    });
}
