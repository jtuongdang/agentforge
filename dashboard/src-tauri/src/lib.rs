use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{self, Write},
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

const ACTIVE_PROFILE_FILE: &str = "active-data-dir";

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct DataProfileStatus {
    active: bool,
    available: bool,
    path: Option<String>,
}

fn profile_marker(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_config_dir()
        .map_err(|err| err.to_string())?
        .join(ACTIVE_PROFILE_FILE))
}

fn valid_restored_data_dir(path: &PathBuf) -> bool {
    path.is_absolute()
        && path.is_dir()
        && path.join("orchestrator.sqlite3").is_file()
        && path.join("../RESTORE-README.txt").is_file()
        && path.join("../repositories").is_dir()
        && path.join("../worktrees").is_dir()
}

fn profile_status_inner(app: &tauri::AppHandle) -> Result<DataProfileStatus, String> {
    let marker = profile_marker(app)?;
    if !marker.exists() {
        return Ok(DataProfileStatus {
            active: false,
            available: true,
            path: None,
        });
    }
    let raw = fs::read_to_string(marker).map_err(|err| err.to_string())?;
    let path = PathBuf::from(raw.trim());
    let available = valid_restored_data_dir(&path);
    let resolved = path.canonicalize().unwrap_or(path);
    Ok(DataProfileStatus {
        active: true,
        available,
        path: Some(resolved.to_string_lossy().to_string()),
    })
}

fn selected_data_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let default = app.path().app_data_dir().map_err(|err| err.to_string())?;
    match profile_status_inner(app) {
        Ok(status) if status.active && status.available => Ok(PathBuf::from(
            status.path.expect("active profile has a path"),
        )),
        Ok(status) if status.active => {
            eprintln!(
                "Restored data profile is missing or invalid; using the original application data directory."
            );
            Ok(default)
        }
        Ok(_) => Ok(default),
        Err(error) => {
            eprintln!(
                "Could not read active data profile ({error}); using the original application data directory."
            );
            Ok(default)
        }
    }
}

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
    let checkpoint_dir = selected_data_dir(&app)?.join("checkpoints");
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

#[tauri::command]
fn data_profile_status(app: tauri::AppHandle) -> Result<DataProfileStatus, String> {
    profile_status_inner(&app)
}

#[tauri::command]
fn activate_restored_profile(app: tauri::AppHandle, data_dir: String) -> Result<(), String> {
    let selected = PathBuf::from(data_dir)
        .canonicalize()
        .map_err(|err| err.to_string())?;
    if !valid_restored_data_dir(&selected) {
        return Err("The selected folder is not a valid restored DevAgent profile".to_string());
    }
    let marker = profile_marker(&app)?;
    if marker.exists() {
        return Err("A restored profile is already selected. Return to the original profile before switching again.".to_string());
    }
    let parent = marker
        .parent()
        .ok_or_else(|| "Application config directory is unavailable".to_string())?;
    fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    let temporary = parent.join(format!(".{ACTIVE_PROFILE_FILE}.{}.tmp", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|err| err.to_string())?;
    if let Err(error) = file
        .write_all(selected.to_string_lossy().as_bytes())
        .and_then(|_| file.sync_all())
        .and_then(|_| fs::rename(&temporary, &marker))
    {
        let _ = fs::remove_file(&temporary);
        return Err(error.to_string());
    }
    app.exit(0);
    Ok(())
}

#[tauri::command]
fn reset_data_profile(app: tauri::AppHandle) -> Result<(), String> {
    let marker = profile_marker(&app)?;
    if marker.exists() {
        fs::remove_file(marker).map_err(|err| err.to_string())?;
    }
    app.exit(0);
    Ok(())
}

fn start_backend(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let inherited_path = env::var("PATH").unwrap_or_default();
    let home = env::var("HOME").unwrap_or_default();
    let desktop_path =
        format!("{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:{inherited_path}");
    let data_dir = selected_data_dir(&app.handle()).map_err(io::Error::other)?;
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
            choose_checkpoint_restore_paths,
            data_profile_status,
            activate_restored_profile,
            reset_data_profile
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
