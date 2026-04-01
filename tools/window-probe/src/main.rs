use serde_json::json;
use x11rb::connection::Connection;
use x11rb::protocol::xproto::{AtomEnum, ConnectionExt};

fn intern_atom<C: Connection>(conn: &C, name: &str) -> Result<u32, String> {
    conn.intern_atom(false, name.as_bytes())
        .map_err(|e| e.to_string())?
        .reply()
        .map_err(|e| e.to_string())
        .map(|r| r.atom)
}

fn get_active_window<C: Connection>(conn: &C, root: u32, atom_active: u32) -> Result<u32, String> {
    let reply = conn
        .get_property(false, root, atom_active, AtomEnum::WINDOW, 0, 1)
        .map_err(|e| e.to_string())?
        .reply()
        .map_err(|e| e.to_string())?;

    let mut values = reply
        .value32()
        .ok_or_else(|| String::from("active-window property format is not 32-bit"))?;

    values
        .next()
        .ok_or_else(|| String::from("no active window id reported"))
}

fn get_text_property<C: Connection>(
    conn: &C,
    window: u32,
    property: u32,
    property_type: u32,
) -> Option<String> {
    let reply = conn
        .get_property(false, window, property, property_type, 0, 4096)
        .ok()?
        .reply()
        .ok()?;

    if reply.value.is_empty() {
        return None;
    }

    let value = String::from_utf8_lossy(&reply.value)
        .trim_matches('\0')
        .trim()
        .to_string();

    if value.is_empty() {
        None
    } else {
        Some(value)
    }
}

fn get_wm_class<C: Connection>(conn: &C, window: u32) -> (String, String) {
    let reply = match conn
        .get_property(false, window, AtomEnum::WM_CLASS, AtomEnum::STRING, 0, 4096)
        .ok()
        .and_then(|cookie| cookie.reply().ok())
    {
        Some(r) => r,
        None => return (String::new(), String::new()),
    };

    let parts: Vec<String> = reply
        .value
        .split(|b| *b == 0)
        .filter_map(|segment| {
            let value = String::from_utf8_lossy(segment).trim().to_string();
            if value.is_empty() {
                None
            } else {
                Some(value)
            }
        })
        .collect();

    let instance = parts.first().cloned().unwrap_or_default();
    let class_name = parts.get(1).cloned().unwrap_or_else(|| instance.clone());
    (instance, class_name)
}

fn main() {
    let (conn, screen_num) = match x11rb::connect(None) {
        Ok(v) => v,
        Err(err) => {
            println!("{}", json!({"ok": false, "error": err.to_string()}));
            std::process::exit(1);
        }
    };

    let screen = match conn.setup().roots.get(screen_num) {
        Some(s) => s,
        None => {
            println!("{}", json!({"ok": false, "error": "invalid X11 screen"}));
            std::process::exit(1);
        }
    };

    let atom_active = match intern_atom(&conn, "_NET_ACTIVE_WINDOW") {
        Ok(a) => a,
        Err(err) => {
            println!("{}", json!({"ok": false, "error": err}));
            std::process::exit(1);
        }
    };

    let atom_net_wm_name = match intern_atom(&conn, "_NET_WM_NAME") {
        Ok(a) => a,
        Err(err) => {
            println!("{}", json!({"ok": false, "error": err}));
            std::process::exit(1);
        }
    };

    let atom_utf8_string = match intern_atom(&conn, "UTF8_STRING") {
        Ok(a) => a,
        Err(err) => {
            println!("{}", json!({"ok": false, "error": err}));
            std::process::exit(1);
        }
    };

    let active_window = match get_active_window(&conn, screen.root, atom_active) {
        Ok(w) => w,
        Err(err) => {
            println!("{}", json!({"ok": false, "error": err}));
            std::process::exit(1);
        }
    };

    if active_window == 0 {
        println!("{}", json!({"ok": false, "error": "active window is 0"}));
        std::process::exit(1);
    }

    let title = get_text_property(&conn, active_window, atom_net_wm_name, atom_utf8_string)
        .or_else(|| {
            get_text_property(
                &conn,
                active_window,
                AtomEnum::WM_NAME.into(),
                AtomEnum::STRING.into(),
            )
        })
        .unwrap_or_default();

    let (instance, class_name) = get_wm_class(&conn, active_window);

    println!(
        "{}",
        json!({
            "ok": true,
            "title": title,
            "class": class_name,
            "instance": instance,
            "window": format!("0x{active_window:08x}"),
        })
    );
}
