import base64, json, os, re, sys, time, uuid
from flask import Flask, jsonify, render_template_string, request, send_from_directory
from flask_cors import CORS

project_home = "/home/Nidishwar/mysite"
if project_home not in sys.path:
    sys.path = [project_home] + sys.path

app = Flask(__name__)
CORS(app)

DATA_FILE = os.path.join(project_home, "forge_network_data.json")
UPLOADS_DIR = os.path.join(project_home, "uploads")
RESET_FLAG_FILE = os.path.join(project_home, ".forge_reset_v5_done")

DEFAULT_DATA = {
    "users": {},
    "logged_in_user": None,
    "posts": [],
    "jobs": [],
    "messages": [],
    "notifications": [],
}

BLOCKED_JOB_TITLES = {
    "python & backend intern",
    "frontend & ui engineer intern",
    "backend developer",
}

MAX_POST_CHARS = 500
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_CHAT_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 250 * 1024 * 1024
ONLINE_WINDOW_SECONDS = 180

EXT_MAP = {
    "jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "webp": ".webp",
    "gif": ".gif", "mp4": ".mp4", "quicktime": ".mov", "webm": ".webm",
}


def ensure_dirs():
    os.makedirs(project_home, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)


def save_data(data):
    ensure_dirs()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def reset_saved_data_once():
    ensure_dirs()
    if not os.path.exists(RESET_FLAG_FILE):
        save_data(json.loads(json.dumps(DEFAULT_DATA)))
        with open(RESET_FLAG_FILE, "w", encoding="utf-8") as f:
            f.write("reset_done")


reset_saved_data_once()


def load_data():
    ensure_dirs()
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = json.loads(json.dumps(DEFAULT_DATA))
        save_data(data)
        return data

    if not isinstance(data, dict):
        data = json.loads(json.dumps(DEFAULT_DATA))
    for k, v in DEFAULT_DATA.items():
        if k not in data:
            data[k] = json.loads(json.dumps(v))
    for list_key in ("posts", "jobs", "messages", "notifications"):
        if not isinstance(data.get(list_key), list):
            data[list_key] = []
    if not isinstance(data.get("users"), dict):
        data["users"] = {}
    return data


def touch_user(db, username):
    if username and username in db.get("users", {}):
        db["users"][username]["last_seen"] = int(time.time())


def get_followers_count(users_map, target_username):
    if not target_username:
        return 0
    return sum(
        1 for uname, u in users_map.items()
        if uname != target_username and target_username in (u.get("connections") or [])
    )


def public_profile(user_record, users_map=None):
    uname = user_record.get("username", "")
    following_list = user_record.get("connections", [])
    followers_cnt = get_followers_count(users_map or {}, uname)
    last_seen = int(user_record.get("last_seen", 0) or 0)
    is_online = (int(time.time()) - last_seen) <= ONLINE_WINDOW_SECONDS if last_seen else False
    return {
        "username": uname,
        "name": user_record.get("name", ""),
        "email": user_record.get("email", ""),
        "role": user_record.get("role", "Student"),
        "avatar": user_record.get("avatar", ""),
        "programme": user_record.get("programme", ""),
        "course": user_record.get("course", user_record.get("programme", "")),
        "year": user_record.get("year", ""),
        "bio": user_record.get("bio", ""),
        "skills": user_record.get("skills", []),
        "connections": following_list,
        "following_count": len(following_list),
        "followers_count": followers_cnt,
        "connection_count": len(following_list),
        "is_online": is_online,
    }


def add_notification(db, sender_name, sender_username=""):
    clean_name = str(sender_name or "").strip()
    clean_uname = str(sender_username or "").strip()
    if not clean_name and not clean_uname:
        return
    notifs = db.setdefault("notifications", [])
    ids = [int(n.get("id", 0)) for n in notifs if str(n.get("id", "")).isdigit()]
    notifs.insert(0, {"id": (max(ids) + 1) if ids else 1, "name": clean_name or clean_uname, "username": clean_uname})


def write_media_bytes(raw, ext):
    ensure_dirs()
    fname = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOADS_DIR, fname), "wb") as out:
        out.write(raw)
    return f"/media/{fname}"


def save_uploaded_file_obj(file_storage, allowed_prefixes, max_bytes, default_ext):
    mimetype = (file_storage.mimetype or "").lower()
    if not any(mimetype.startswith(p) for p in allowed_prefixes):
        return None, "Unsupported file type."
    raw = file_storage.read()
    if not raw:
        return None, "Empty file."
    if len(raw) > max_bytes:
        return None, f"File exceeds maximum size of {max_bytes // (1024 * 1024)} MB."
    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    if not ext or not re.match(r"^\.[a-z0-9]{1,8}$", ext):
        ext = default_ext
    return write_media_bytes(raw, ext), None


def save_data_url_if_present(data_url, allowed_prefixes, max_bytes, default_ext):
    if not data_url or not isinstance(data_url, str):
        return None, None
    if data_url.startswith("/media/"):
        return data_url, None
    m = re.match(r"^data:([a-zA-Z0-9.+/-]+);base64,(.+)$", data_url, re.DOTALL)
    if not m:
        return None, "Invalid media data."
    mime = m.group(1).lower()
    if not any(mime.startswith(p) for p in allowed_prefixes):
        return None, "Unsupported media format."
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return None, "Corrupted media data."
    if len(raw) > max_bytes:
        return None, f"File exceeds maximum size of {max_bytes // (1024 * 1024)} MB."
    subtype = mime.split("/")[-1].split(";")[0]
    return write_media_bytes(raw, EXT_MAP.get(subtype, default_ext)), None


def parse_skills(raw_skills):
    if isinstance(raw_skills, str):
        return [s.strip() for s in raw_skills.split(",") if s.strip()]
    if isinstance(raw_skills, list):
        return [str(s).strip() for s in raw_skills if str(s).strip()]
    return []


def next_item_id(items):
    ids = [int(x.get("id", 0)) for x in items if str(x.get("id", "")).isdigit()]
    return (max(ids) + 1) if ids else 1


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Forge</title>
  <style>
    :root {
      --bg-page: #f4f2fc;
      --bg-surface: #ffffff;
      --bg-subtle: #efeafe;
      --bg-input: #f6f4fe;
      --text-primary: #171226;
      --text-secondary: #5c5475;
      --border-color: #e2daf8;
      --accent: #6d28d9;
      --accent-hover: #5b21b6;
      --accent-soft: #ede9fe;
      --accent-text: #ffffff;
      --danger: #dc2626;
      --shadow: 0 8px 24px rgba(23, 18, 38, 0.06);
    }
    html[data-theme="dark"] {
      --bg-page: #0f0c18;
      --bg-surface: #181326;
      --bg-subtle: #221b36;
      --bg-input: #201933;
      --text-primary: #f5f3ff;
      --text-secondary: #b4a9d4;
      --border-color: #2f254a;
      --accent: #8b5cf6;
      --accent-hover: #7c3aed;
      --accent-soft: #2a1e47;
      --accent-text: #ffffff;
      --danger: #f87171;
      --shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: "Inter", system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }
    body { background: var(--bg-page); color: var(--text-primary); transition: background-color 0.2s, color 0.2s; -webkit-font-smoothing: antialiased; }
    .hidden { display: none !important; }

    .auth-wrap { max-width: 440px; margin: 36px auto; background: var(--bg-surface); padding: 24px; border-radius: 16px; border: 1px solid var(--border-color); box-shadow: var(--shadow); }
    .brand { color: var(--accent); font-size: 28px; font-weight: 800; letter-spacing: -0.5px; margin-bottom: 4px; }
    .brand-sub { color: var(--text-secondary); font-size: 13px; margin-bottom: 18px; }
    .auth-tabs { display: flex; gap: 8px; margin-bottom: 16px; background: var(--bg-subtle); padding: 4px; border-radius: 10px; }
    .auth-tabs button { flex: 1; padding: 9px; border: none; border-radius: 8px; font-weight: 600; font-size: 13.5px; cursor: pointer; background: transparent; color: var(--text-secondary); transition: all 0.15s; }
    .auth-tabs button.active { background: var(--accent); color: var(--accent-text); box-shadow: 0 2px 8px rgba(109, 40, 217, 0.22); }
    .field { margin-bottom: 10px; }
    .field-label { display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 4px; }
    input, textarea, select { width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border-color); font-size: 14px; background: var(--bg-input); color: var(--text-primary); transition: border-color 0.15s, background-color 0.15s; }
    input:focus, textarea:focus, select:focus { outline: none; border-color: var(--accent); background: var(--bg-surface); }

    .primary-btn { width: 100%; padding: 11px 16px; border-radius: 999px; border: none; background: var(--accent); color: var(--accent-text); font-weight: 600; font-size: 14px; cursor: pointer; margin-top: 4px; transition: background-color 0.15s; }
    .primary-btn:hover { background: var(--accent-hover); }
    .primary-btn:disabled { opacity: 0.45; cursor: not-allowed; }
    .status-msg { margin-top: 12px; padding: 10px 12px; border-radius: 10px; font-size: 13px; background: var(--accent-soft); color: var(--text-primary); display: none; }
    .status-msg.visible { display: block; }
    .status-msg.error { background: rgba(220, 38, 38, 0.12); color: var(--danger); }

    .top-header-wrap { position: sticky; top: 0; z-index: 900; background: var(--bg-surface); border-bottom: 1px solid var(--border-color); backdrop-filter: blur(8px); }
    .top-bar { max-width: 760px; margin: 0 auto; padding: 10px 14px; display: flex; align-items: center; gap: 10px; }
    .top-avatar { width: 36px; height: 36px; border-radius: 50%; object-fit: cover; background: var(--accent-soft); flex-shrink: 0; cursor: pointer; border: 1.5px solid var(--border-color); }
    .top-brand-title { flex: 1; font-size: 19px; font-weight: 800; color: var(--accent); letter-spacing: -0.3px; cursor: pointer; }
    .home-top-search-bar { width: 100%; background: var(--bg-surface); border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 14px; font-size: 14px; color: var(--text-primary); margin-top: 8px; box-shadow: var(--shadow); }
    .home-top-search-bar:focus { outline: none; border-color: var(--accent); }

    .top-icon-btn { background: var(--bg-subtle); border: 1px solid var(--border-color); font-size: 19px; font-weight: 700; cursor: pointer; width: 36px; height: 36px; border-radius: 50%; position: relative; color: var(--text-primary); flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; transition: all 0.15s; }
    .top-icon-btn:hover, .top-icon-btn.active { background: var(--accent); border-color: var(--accent); color: var(--accent-text); }
    .top-nav-bar { max-width: 760px; margin: 0 auto; display: flex; justify-content: space-around; align-items: center; padding: 2px 8px 0; border-top: 1px solid var(--border-color); }
    .nav-btn { flex: 1; background: transparent; border: none; border-bottom: 2.5px solid transparent; color: var(--text-secondary); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; font-size: 11.5px; font-weight: 600; cursor: pointer; padding: 8px 4px; transition: color 0.15s, border-color 0.15s; }
    .nav-icon-svg { width: 20px; height: 20px; flex-shrink: 0; color: currentColor; }
    .nav-btn:hover { color: var(--accent); }
    .nav-btn.active { color: var(--accent); font-weight: 700; border-bottom-color: var(--accent); }

    .container { max-width: 720px; margin: 0 auto; padding: 6px 10px 32px; min-width: 0; }
    .card { background: var(--bg-surface); padding: 15px 17px; margin-top: 10px; border: 1px solid var(--border-color); border-radius: 14px; box-shadow: var(--shadow); }
    .post-card-clickable { cursor: pointer; }
    .composer-meta-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
    .media-pick-group { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .photo-upload-label { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--border-color); background: var(--bg-subtle); color: var(--text-primary); font-size: 12px; font-weight: 600; cursor: pointer; }
    .photo-upload-label:hover { border-color: var(--accent); color: var(--accent); }
    .char-counter { font-size: 12px; font-weight: 600; color: var(--text-secondary); }
    .char-counter.limit-near { color: var(--danger); }
    .image-preview-wrap { position: relative; margin-bottom: 10px; display: block; }
    .image-preview-img { max-width: 100%; max-height: 220px; border-radius: 10px; border: 1px solid var(--border-color); display: block; }
    .video-preview-player { width: 100%; max-height: 260px; border-radius: 10px; border: 1px solid var(--border-color); background: #000; display: block; }
    .remove-image-btn { margin-top: 6px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--danger); background: transparent; color: var(--danger); font-size: 11px; font-weight: 600; cursor: pointer; }
    .post-image-media { margin-top: 10px; width: 100%; max-height: 380px; object-fit: cover; border-radius: 12px; border: 1px solid var(--border-color); display: block; }
    .post-video-media { margin-top: 10px; width: 100%; max-height: 400px; border-radius: 12px; border: 1px solid var(--border-color); background: #000; display: block; }

    .promo-card { display: flex; justify-content: space-between; align-items: center; gap: 14px; }
    .promo-title { font-size: 16.5px; font-weight: 700; line-height: 1.35; color: var(--text-primary); }
    .pill-btn { margin-top: 10px; padding: 7px 15px; border-radius: 999px; border: 1px solid var(--border-color); background: var(--bg-subtle); color: var(--text-primary); font-weight: 600; font-size: 12.5px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; transition: all 0.15s; }
    .pill-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
    .pill-btn.active { background: var(--accent); border-color: var(--accent); color: var(--accent-text); }
    .promo-badge { width: 60px; height: 60px; border-radius: 16px; background: var(--accent-soft); color: var(--accent); display: flex; align-items: center; justify-content: center; font-size: 11.5px; font-weight: 800; letter-spacing: 0.6px; flex-shrink: 0; border: 1px solid var(--border-color); }

    .profile-top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border-color); gap: 8px; }
    .profile-top-actions { display: flex; align-items: center; gap: 8px; }
    .profile-settings-btn { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--border-color); background: var(--bg-subtle); color: var(--text-primary); font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.15s; }
    .profile-settings-btn:hover, .profile-settings-btn.active { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }

    .profile-header-row { display: flex; align-items: center; gap: 18px; }
    .profile-dp-wrap { position: relative; flex-shrink: 0; display: flex; flex-direction: column; align-items: center; gap: 6px; }
    .profile-dp { width: 78px; height: 78px; border-radius: 50%; object-fit: cover; border: 2px solid var(--accent); flex-shrink: 0; background: var(--accent-soft); cursor: pointer; }
    .change-dp-btn { padding: 3px 10px; border-radius: 999px; border: 1px solid var(--border-color); background: var(--bg-subtle); color: var(--text-primary); font-size: 11px; font-weight: 600; cursor: pointer; }
    .change-dp-btn:hover { border-color: var(--accent); color: var(--accent); }
    .profile-stats-side { flex: 1; display: flex; justify-content: space-around; text-align: center; gap: 8px; }
    .stat-box { cursor: pointer; padding: 6px 4px; border-radius: 10px; transition: background-color 0.15s; }
    .stat-box:hover { background: var(--bg-subtle); }
    .stat-num { font-size: 18px; font-weight: 800; color: var(--text-primary); }
    .stat-label { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }
    .my-posts-title { font-size: 14.5px; font-weight: 700; margin-top: 14px; margin-bottom: 4px; padding: 0 4px; color: var(--text-secondary); }

    .post-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
    .post-author-wrap { display: flex; gap: 10px; }
    .post-author-trigger { display: flex; gap: 10px; cursor: pointer; }
    .post-avatar { width: 42px; height: 42px; border-radius: 50%; object-fit: cover; border: 1px solid var(--border-color); flex-shrink: 0; }
    .post-name { font-weight: 700; font-size: 14px; color: var(--text-primary); }
    .post-sub { font-size: 12px; color: var(--text-secondary); line-height: 1.4; }
    .post-text { margin-top: 10px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; color: var(--text-primary); }
    .post-stats { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-secondary); padding: 10px 0 7px; border-bottom: 1px solid var(--border-color); }
    .post-actions { display: flex; justify-content: space-around; padding-top: 6px; }
    .post-action-btn { background: none; border: none; color: var(--text-secondary); font-size: 12.5px; font-weight: 600; cursor: pointer; padding: 6px 8px; border-radius: 8px; display: inline-flex; align-items: center; gap: 5px; transition: all 0.15s; }
    .post-action-btn:hover { background: var(--bg-subtle); color: var(--accent); }
    .post-action-btn.liked, .post-action-btn.reposted { color: var(--accent); font-weight: 700; }
    .delete-post-btn { background: none; border: 1px solid var(--border-color); color: var(--danger); font-size: 11.5px; font-weight: 600; cursor: pointer; padding: 4px 10px; border-radius: 999px; display: inline-flex; align-items: center; gap: 4px; }
    .delete-post-btn:hover { background: var(--bg-subtle); border-color: var(--danger); }

    .comments-panel { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border-color); }
    .comments-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
    .comment-item { background: var(--bg-subtle); border: 1px solid var(--border-color); border-radius: 8px; padding: 7px 10px; font-size: 12px; color: var(--text-primary); }
    .comment-author { font-weight: 700; color: var(--accent); margin-right: 6px; }
    .comment-form-row { display: flex; gap: 8px; align-items: center; }
    .comment-form-row input { padding: 8px 10px; font-size: 13px; }
    .comment-submit-btn { padding: 8px 14px; border-radius: 999px; border: none; background: var(--accent); color: var(--accent-text); font-size: 12px; font-weight: 600; cursor: pointer; flex-shrink: 0; }
    .comment-submit-btn:hover { background: var(--accent-hover); }

    .list-item { padding: 12px 0; border-top: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; gap: 10px; }
    .clickable-member { cursor: pointer; }
    .connect-btn { padding: 6px 14px; border-radius: 999px; border: 1px solid var(--accent); background: transparent; color: var(--accent); font-weight: 600; font-size: 12px; cursor: pointer; flex-shrink: 0; transition: all 0.15s; }
    .connect-btn:hover { background: var(--accent-soft); }
    .connect-btn.connected { background: var(--accent); color: var(--accent-text); }
    .skill-tag { display: inline-block; background: var(--accent-soft); color: var(--accent); font-size: 11.5px; font-weight: 600; padding: 3px 10px; border-radius: 999px; margin: 4px 4px 0 0; }

    .peer-avatar-col { display: flex; flex-direction: column; align-items: center; gap: 3px; flex-shrink: 0; cursor: pointer; }
    .online-badge { font-size: 9px; font-weight: 800; letter-spacing: 0.4px; color: #16a34a; background: rgba(22, 163, 74, 0.14); border: 1px solid rgba(22, 163, 74, 0.35); padding: 1px 6px; border-radius: 999px; line-height: 1.25; }

    .chat-list-wrap { margin-top: 10px; display: flex; flex-direction: column; }
    .chat-blank-space { min-height: 220px; }
    .chat-thread-row { display: flex; align-items: center; gap: 12px; padding: 11px 8px; border-top: 1px solid var(--border-color); cursor: pointer; border-radius: 10px; transition: background-color 0.15s; }
    .chat-thread-row:hover { background: var(--bg-subtle); }
    .chat-thread-avatar { width: 46px; height: 46px; border-radius: 50%; object-fit: cover; border: 1.5px solid var(--border-color); flex-shrink: 0; background: var(--accent-soft); }
    .chat-thread-info { flex: 1; min-width: 0; }
    .chat-thread-name { font-size: 14px; font-weight: 700; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .chat-thread-handle { font-size: 12px; font-weight: 400; color: var(--text-secondary); margin-left: 4px; }
    .chat-thread-preview { font-size: 12px; color: var(--text-secondary); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .chat-thread-chevron { color: var(--text-secondary); font-size: 16px; font-weight: 700; flex-shrink: 0; }
    .chat-convo-header { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-top: 1px solid var(--border-color); margin-top: 8px; }
    .chat-exit-arrow-btn { width: 34px; height: 34px; border-radius: 50%; border: 1px solid var(--border-color); background: var(--bg-subtle); color: var(--text-primary); display: inline-flex; align-items: center; justify-content: center; cursor: pointer; flex-shrink: 0; }
    .chat-exit-arrow-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
    .chat-convo-peer { display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1; cursor: pointer; }

    .chat-layout { display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
    .chat-messages-box { min-height: 220px; max-height: 360px; overflow-y: auto; padding: 12px; border-radius: 12px; background: var(--bg-subtle); border: 1px solid var(--border-color); display: flex; flex-direction: column; gap: 8px; }
    .msg-bubble { max-width: 82%; padding: 8px 12px; border-radius: 12px; font-size: 13px; line-height: 1.4; word-break: break-word; }
    .msg-bubble.sent { align-self: flex-end; background: var(--accent); color: var(--accent-text); }
    .msg-bubble.received { align-self: flex-start; background: var(--bg-surface); color: var(--text-primary); border: 1px solid var(--border-color); }
    .chat-msg-image { margin-top: 6px; max-width: 100%; max-height: 220px; border-radius: 8px; display: block; object-fit: cover; }
    .chat-photo-preview-bar { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 10px; background: var(--bg-subtle); border: 1px solid var(--border-color); }
    .chat-photo-thumb { width: 48px; height: 48px; border-radius: 8px; object-fit: cover; border: 1px solid var(--border-color); flex-shrink: 0; }
    .chat-photo-pick-btn { width: 38px; height: 38px; border-radius: 50%; border: 1px solid var(--border-color); background: var(--bg-subtle); color: var(--text-primary); display: inline-flex; align-items: center; justify-content: center; cursor: pointer; flex-shrink: 0; }
    .chat-photo-pick-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
    .msg-meta { font-size: 10px; opacity: 0.78; margin-top: 4px; }
    .chat-send-row { display: flex; gap: 8px; align-items: center; }
    .chat-send-row input { flex: 1; }
    .chat-send-btn { padding: 10px 18px; border-radius: 999px; border: none; background: var(--accent); color: var(--accent-text); font-weight: 600; font-size: 13px; cursor: pointer; flex-shrink: 0; }
    .chat-send-btn:hover { background: var(--accent-hover); }

    .settings-section-title { font-size: 14.5px; font-weight: 700; color: var(--text-primary); margin-bottom: 4px; }
    .password-trigger-btn { width: 100%; display: flex; justify-content: space-between; align-items: center; padding: 12px 14px; border-radius: 10px; border: 1px solid var(--border-color); background: var(--bg-subtle); color: var(--text-primary); font-size: 14px; font-weight: 700; cursor: pointer; }
    .password-trigger-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
    .theme-toggle-row { display: flex; gap: 10px; margin-top: 10px; }
    .theme-option-btn { flex: 1; padding: 10px 14px; border-radius: 10px; border: 1px solid var(--border-color); background: var(--bg-subtle); color: var(--text-primary); font-weight: 600; font-size: 13px; cursor: pointer; }
    .theme-option-btn.active { background: var(--accent); border-color: var(--accent); color: var(--accent-text); }
    .account-row { padding: 10px 0; border-top: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; gap: 10px; }
    .active-pill { font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); }
    .quick-add-box { margin-top: 12px; padding: 14px; border-radius: 12px; background: var(--bg-subtle); border: 1px solid var(--border-color); }

    /* Send Sheet Modal */
    .overlay-backdrop { position: fixed; inset: 0; z-index: 1200; background: rgba(10, 8, 18, 0.65); display: flex; align-items: flex-end; justify-content: center; }
    .overlay-backdrop.center { align-items: center; padding: 14px; overflow-y: auto; }
    .share-sheet { width: 100%; max-width: 640px; background: var(--bg-surface); border-top-left-radius: 20px; border-top-right-radius: 20px; border: 1px solid var(--border-color); padding: 14px 16px 22px; max-height: 82vh; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
    .share-handle { width: 38px; height: 4px; border-radius: 999px; background: var(--border-color); margin: 0 auto 2px; }
    .share-header { display: flex; justify-content: space-between; align-items: center; }
    .share-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 4px; }
    .share-person-btn { background: transparent; border: none; color: var(--text-primary); display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 6px 4px; border-radius: 12px; cursor: pointer; text-align: center; }
    .share-person-btn:hover { background: var(--bg-subtle); }
    .share-person-avatar { width: 56px; height: 56px; border-radius: 50%; object-fit: cover; border: 1.5px solid var(--border-color); background: var(--accent-soft); }
    .share-person-name { font-size: 12px; font-weight: 600; width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* Full Page Post Preview */
    .preview-modal-card { width: 100%; max-width: 700px; max-height: 92vh; overflow-y: auto; background: var(--bg-surface); border: 1px solid var(--border-color); border-radius: 16px; padding: 18px; box-shadow: var(--shadow); }
    .preview-top-bar { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding-bottom: 12px; border-bottom: 1px solid var(--border-color); }
    .preview-full-img { width: 100%; max-height: 70vh; object-fit: contain; border-radius: 12px; margin-top: 12px; background: var(--bg-subtle); }
    .preview-full-vid { width: 100%; max-height: 70vh; border-radius: 12px; margin-top: 12px; background: #000; }

    @media (max-width: 540px) { .promo-card { flex-direction: column; align-items: flex-start; } }
  </style>
</head>
<body>
  <section id="authSection" class="auth-wrap">
    <div class="brand">Forge</div>
    <div class="brand-sub">Sign in or create your account to continue</div>

    <div class="auth-tabs">
      <button id="showLoginBtn" class="active" type="button">Login</button>
      <button id="showSignupBtn" type="button">Sign Up</button>
    </div>

    <form id="loginForm" autocomplete="on">
      <div class="field"><input type="text" id="loginUsername" name="loginUsername" placeholder="Username" /></div>
      <div class="field"><input type="password" id="loginPassword" name="loginPassword" placeholder="Password" /></div>
      <button class="primary-btn" type="submit">Login</button>
    </form>

    <form id="signupForm" class="hidden" autocomplete="on" novalidate>
      <div class="field">
        <select id="role" name="role" required>
          <option value="">Select Account Type (Student or Job Creator)</option>
          <option value="Student">Student</option>
          <option value="Job Creator">Job Creator</option>
        </select>
      </div>
      <div class="field"><input type="text" id="username" name="username" placeholder="Username (min 3 chars, letters/numbers/_)" required /></div>
      <div class="field"><input type="password" id="password" name="password" placeholder="Password (min 6 characters)" required /></div>
      <div class="field"><input type="text" id="name" name="name" placeholder="Full Name" required /></div>
      <div class="field"><input type="email" id="email" name="email" placeholder="Valid Email ID (e.g. name@domain.com)" required /></div>
      <div class="field">
        <select id="course" name="course" required>
          <option value="">Select Course</option>
          <option value="B.Tech">B.Tech</option>
          <option value="B.E.">B.E.</option>
          <option value="B.Sc">B.Sc</option>
          <option value="BCA">BCA</option>
        </select>
      </div>
      <div class="field">
        <select id="year" name="year" required>
          <option value="">Select Year</option>
          <option value="1">1</option>
          <option value="2">2</option>
          <option value="3">3</option>
          <option value="4">4</option>
        </select>
      </div>
      <div class="field"><input type="text" id="bio" name="bio" placeholder="Short Bio (min 5 characters)" required /></div>
      <div class="field"><input type="text" id="skills" name="skills" placeholder="Skills (at least 1 skill, comma separated)" required /></div>
      <button class="primary-btn" type="submit">Create Account &amp; Enter</button>
    </form>

    <div id="authMessage" class="status-msg" role="status"></div>
  </section>

  <div id="mainApp" class="hidden">
    <div class="top-header-wrap">
      <header class="top-bar">
        <img id="topAvatar" class="top-avatar" src="https://ui-avatars.com/api/?name=N&background=6d28d9&color=fff" alt="Profile" />
        <div id="topBrandTitle" class="top-brand-title">Forge</div>
        <button id="openNotificationsBtn" class="top-icon-btn hidden" title="Notifications" aria-label="Notifications" type="button">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path>
            <path d="M13.73 21a2 2 0 0 1-3.46 0"></path>
          </svg>
        </button>
        <button id="openPostBtn" class="top-icon-btn" title="Create Post (Home only)" type="button">+</button>
      </header>

      <nav class="top-nav-bar" aria-label="Primary Navigation">
        <button class="nav-btn active" data-tab="homeTab" type="button">
          <svg class="nav-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 10.5L12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1v-9.5z"/>
          </svg>
          <span>Home</span>
        </button>

        <button class="nav-btn" data-tab="jobsTab" type="button">
          <svg class="nav-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect>
            <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"></path>
            <line x1="2" y1="13" x2="22" y2="13"></line>
          </svg>
          <span>Jobs</span>
        </button>

        <button class="nav-btn" data-tab="notificationsTab" type="button">
          <svg class="nav-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <line x1="22" y1="2" x2="11" y2="13"></line>
            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
          </svg>
          <span>Chat</span>
        </button>

        <button class="nav-btn" data-tab="networkTab" type="button">
          <svg class="nav-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9"></circle>
            <polygon points="10 8 16 12 10 16 10 8"></polygon>
          </svg>
          <span>Feed</span>
        </button>

        <button class="nav-btn" data-tab="moreTab" type="button">
          <svg class="nav-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
            <circle cx="12" cy="7" r="4"></circle>
          </svg>
          <span>Profile</span>
        </button>
      </nav>
    </div>

    <main class="container">
      <div id="homeNotificationsBox" class="card hidden">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <h4 style="margin:0;">Notifications</h4>
          <button id="closeHomeNotificationsBtn" class="pill-btn" style="margin-top:0; padding:4px 12px; font-size:12px;" type="button">Close</button>
        </div>
        <div id="homeNotificationsList"></div>
      </div>

      <div id="createPostBox" class="card hidden">
        <h4 style="margin-bottom:8px;">Create New Post</h4>
        <div class="field">
          <textarea id="newPostText" rows="3" maxlength="500" placeholder="Write something (1 to 500 characters required)..."></textarea>
        </div>

        <div class="composer-meta-row">
          <div class="media-pick-group">
            <label class="photo-upload-label" for="postImageInput" title="Max 25 MB">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                <circle cx="8.5" cy="8.5" r="1.5"></circle>
                <polyline points="21 15 16 10 5 21"></polyline>
              </svg>
              <span>Add Photo (25MB)</span>
            </label>
            <input id="postImageInput" type="file" accept="image/*" class="hidden" />

            <label class="photo-upload-label" for="postVideoInput" title="Max 250 MB">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <polygon points="23 7 16 12 23 17 23 7"></polygon>
                <rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect>
              </svg>
              <span>Add Video (250MB)</span>
            </label>
            <input id="postVideoInput" type="file" accept="video/*" class="hidden" />
          </div>
          <span id="postCharCounter" class="char-counter">0 / 500</span>
        </div>

        <div id="postMediaPreviewBox" class="image-preview-wrap hidden">
          <img id="postImagePreviewImg" class="image-preview-img hidden" src="" alt="Selected photo preview" />
          <video id="postVideoPreviewPlayer" class="video-preview-player hidden" controls playsinline></video>
          <button id="removePostMediaBtn" class="remove-image-btn" type="button">Remove Media</button>
        </div>

        <div id="postFeedback" class="post-sub" style="margin-bottom:8px; color:var(--accent);"></div>
        <button id="publishPostBtn" class="primary-btn" type="button" disabled>Post</button>
      </div>

      <section id="homeTab" class="tab-view">
        <input id="homeTopSearchInput" class="home-top-search-bar" type="search" placeholder="Search members, roles, skills, courses..." />
        <div id="homeSearchResultsBox" class="card hidden">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <h4 style="margin:0;">Search Results</h4>
            <button id="clearHomeSearchBtn" class="pill-btn" style="margin-top:0; padding:4px 12px; font-size:12px;" type="button">Clear</button>
          </div>
          <div id="homeSearchResultsList"></div>
        </div>

        <div class="card promo-card">
          <div>
            <div id="welcomePromo" class="promo-title">NIDISHWARREDDY, a complete profile helps the right opportunities find you</div>
            <button id="getStartedBtn" class="pill-btn" type="button">Get started</button>
          </div>
          <div class="promo-badge">FORGE</div>
        </div>
        <div id="feedList"></div>
      </section>

      <section id="jobsTab" class="tab-view hidden">
        <div class="card">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
            <div>
              <h3>Jobs</h3>
              <p class="post-sub" style="margin-top:4px;">Create a new job or view jobs posted here.</p>
            </div>
            <button id="toggleCreateJobBtn" class="pill-btn" style="margin-top:0;" type="button">Create New Job</button>
          </div>

          <div id="createJobBox" class="quick-add-box hidden">
            <h4 style="margin-bottom:8px;">Create New Job</h4>
            <div class="field"><input type="text" id="jobTitleInput" placeholder="Job Title" /></div>
            <div class="field"><input type="text" id="jobCompanyInput" placeholder="Company / Organization" /></div>
            <div class="field"><input type="text" id="jobLocationInput" placeholder="Location" /></div>
            <div class="field"><input type="text" id="jobSkillsInput" placeholder="Skills (comma separated)" /></div>
            <div id="jobFeedback" class="post-sub" style="margin-bottom:8px; color:var(--accent);"></div>
            <button id="publishJobBtn" class="primary-btn" type="button">Publish Job</button>
          </div>

          <div id="jobsList"></div>
        </div>
      </section>

      <section id="networkTab" class="tab-view hidden">
        <div class="card">
          <h3>Feed</h3>
        </div>
        <div id="videoAndUnknownFeedList"></div>
      </section>

      <section id="notificationsTab" class="tab-view hidden">
        <div class="card">
          <h3>Chats</h3>
          <div id="chatListView" class="chat-list-wrap"></div>

          <div id="chatConversationView" class="chat-layout hidden">
            <div class="chat-convo-header">
              <button id="backToChatsListBtn" class="chat-exit-arrow-btn" title="Exit chat to list" aria-label="Exit chat to list" type="button">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <line x1="19" y1="12" x2="5" y2="12"></line>
                  <polyline points="12 19 5 12 12 5"></polyline>
                </svg>
              </button>
              <div id="activeChatPeerTrigger" class="chat-convo-peer">
                <img id="activeChatAvatar" class="chat-thread-avatar" src="https://ui-avatars.com/api/?name=U&background=6d28d9&color=fff" alt="Peer" />
                <div>
                  <div id="activeChatTitle" class="chat-thread-name">Member</div>
                  <div id="activeChatSub" class="post-sub"></div>
                </div>
              </div>
            </div>

            <div id="chatMessagesBox" class="chat-messages-box"></div>

            <div id="chatPhotoPreviewBar" class="chat-photo-preview-bar hidden">
              <img id="chatPhotoThumb" class="chat-photo-thumb" src="" alt="Selected photo" />
              <div style="flex:1; min-width:0;">
                <div id="chatPhotoName" class="chat-thread-name" style="font-size:12px;">Photo</div>
                <div class="post-sub">Max size: 10 MB</div>
              </div>
              <button id="removeChatPhotoBtn" class="remove-image-btn" style="margin-top:0;" type="button">Remove</button>
            </div>

            <div class="chat-send-row">
              <button id="chatPhotoPickBtn" class="chat-photo-pick-btn" title="Send photo (Max 10 MB)" aria-label="Send photo (Max 10 MB)" type="button">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                  <circle cx="8.5" cy="8.5" r="1.5"></circle>
                  <polyline points="21 15 16 10 5 21"></polyline>
                </svg>
              </button>
              <input id="chatPhotoInput" type="file" accept="image/*" class="hidden" />
              <input id="chatMessageInput" type="text" placeholder="Type a message..." />
              <button id="sendChatBtn" class="chat-send-btn" type="button">Send</button>
            </div>
            <div id="chatFeedback" class="post-sub" style="color:var(--accent);"></div>
          </div>
        </div>
      </section>

      <section id="memberProfileTab" class="tab-view hidden">
        <div class="card">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:12px;">
            <h3 style="margin:0;">Member Profile</h3>
            <button id="backFromMemberProfileBtn" class="pill-btn" style="margin-top:0;" type="button">Back</button>
          </div>
          <div id="memberProfileContent"></div>
        </div>
      </section>

      <section id="followTab" class="tab-view hidden">
        <div class="card">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
            <div>
              <h3 id="followTabHeading">Followers &amp; Following</h3>
              <p class="post-sub" style="margin-top:4px;">View your followers list and following list.</p>
            </div>
            <button id="backFromFollowTabBtn" class="pill-btn" style="margin-top:0;" type="button">Back to Profile</button>
          </div>

          <div class="theme-toggle-row" style="margin-top:14px;">
            <button id="switchToFollowersListBtn" class="theme-option-btn active" type="button">Followers List</button>
            <button id="switchToFollowingListBtn" class="theme-option-btn" type="button">Following List</button>
          </div>

          <div id="followTabListItems" style="margin-top:8px;"></div>
        </div>
      </section>

      <section id="moreTab" class="tab-view hidden">
        <div class="card">
          <div class="profile-top-bar">
            <h3 style="margin:0;">Profile</h3>
            <div class="profile-top-actions">
              <button id="openEditProfileBtn" class="profile-settings-btn" type="button" title="Edit Profile">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M12 20h9"></path>
                  <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
                </svg>
                <span>Edit</span>
              </button>
              <button id="openProfileSettingsBtn" class="profile-settings-btn" type="button" title="Settings">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="3"></circle>
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"></path>
                </svg>
                <span>Settings</span>
              </button>
            </div>
          </div>

          <div class="profile-header-row">
            <div class="profile-dp-wrap">
              <img id="profileDetailAvatar" class="profile-dp" src="https://ui-avatars.com/api/?name=N&background=6d28d9&color=fff" alt="Profile DP" title="Tap to pick photo from gallery" />
              <button id="changeProfilePicBtn" class="change-dp-btn" type="button">Profile Pic</button>
              <input id="profilePicGalleryInput" type="file" accept="image/*" class="hidden" />
            </div>
            <div class="profile-stats-side">
              <div class="stat-box" id="statFollowersBox">
                <div id="followersCount" class="stat-num">0</div>
                <div class="stat-label">Followers</div>
              </div>
              <div class="stat-box" id="statFollowingBox">
                <div id="followingCount" class="stat-num">0</div>
                <div class="stat-label">Following</div>
              </div>
              <div class="stat-box" id="statPostsBox">
                <div id="userPostsCount" class="stat-num">0</div>
                <div class="stat-label">Posts</div>
              </div>
            </div>
          </div>

          <div id="profilePicFeedback" class="post-sub" style="margin-top:6px; color:var(--accent);"></div>

          <div style="margin-top:10px;">
            <h4 id="moreProfileName">Profile</h4>
            <div id="moreProfileMeta" class="post-sub" style="margin-top:2px;"></div>
            <p id="moreProfileBio" class="post-text"></p>
            <div id="moreProfileSkills" style="margin-top:8px;"></div>
          </div>

          <div id="editProfileBox" class="quick-add-box hidden" style="margin-top:14px;">
            <h4 style="margin-bottom:10px;">Edit Profile</h4>
            <div class="field">
              <label class="field-label" for="editRoleSelect">Role</label>
              <select id="editRoleSelect">
                <option value="Student">Student</option>
                <option value="Job Creator">Job Creator</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label" for="editNameInput">Name</label>
              <input type="text" id="editNameInput" placeholder="Enter full name" />
            </div>
            <div class="field">
              <label class="field-label" for="editUsernameInput">Username</label>
              <input type="text" id="editUsernameInput" placeholder="Enter username (letters, numbers, _)" />
            </div>
            <div class="field">
              <label class="field-label" for="editBioInput">Bio</label>
              <input type="text" id="editBioInput" placeholder="Short bio" />
            </div>
            <div class="field">
              <label class="field-label" for="editSkillsInput">Skills (comma separated)</label>
              <input type="text" id="editSkillsInput" placeholder="e.g. Python, C++, CAD" />
            </div>
            <div id="editProfileFeedback" class="post-sub" style="margin-bottom:8px; color:var(--accent);"></div>
            <div style="display:flex; gap:8px;">
              <button id="saveProfileEditBtn" class="primary-btn" style="margin-top:0; flex:1;" type="button">Save Changes</button>
              <button id="cancelProfileEditBtn" class="pill-btn" style="margin-top:0;" type="button">Cancel</button>
            </div>
          </div>

          <div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
            <button id="profileRepostsBtn" class="pill-btn" type="button">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <polyline points="17 1 21 5 17 9"></polyline>
                <path d="M3 11V9a4 4 0 0 1 4-4h14"></path>
                <polyline points="7 23 3 19 7 15"></polyline>
                <path d="M21 13v2a4 4 0 0 1-4 4H3"></path>
              </svg>
              <span>Repost</span>
            </button>
            <button id="profileMyPostsBtn" class="pill-btn active" type="button">
              <span>My Posts</span>
            </button>
            <button id="logoutBtn" class="pill-btn" type="button">Logout</button>
          </div>
        </div>

        <div id="profileRepostsSection" class="card hidden">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <h4 style="margin:0;">Reposted Posts</h4>
            <button id="closeProfileRepostsBtn" class="pill-btn" style="margin-top:0; padding:4px 12px; font-size:12px;" type="button">Hide</button>
          </div>
          <div id="profileRepostsList"></div>
        </div>

        <div id="profileMyPostsSection">
          <div class="my-posts-title">My Posts</div>
          <div id="myPostsList"></div>
        </div>
      </section>

      <section id="settingsTab" class="tab-view hidden">
        <div class="card">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
            <div>
              <h3>Settings</h3>
              <p class="post-sub" style="margin-top:4px;">Appearance, password, and account management.</p>
            </div>
            <button id="backToProfileBtn" class="pill-btn" style="margin-top:0;" type="button">Back to Profile</button>
          </div>

          <div style="margin-top:16px;">
            <div class="settings-section-title">Theme Mode</div>
            <p class="post-sub">Choose between Light Mode and Dark Mode.</p>
            <div class="theme-toggle-row">
              <button id="lightModeBtn" class="theme-option-btn active" type="button">Light Mode</button>
              <button id="darkModeBtn" class="theme-option-btn" type="button">Dark Mode</button>
            </div>
          </div>
        </div>

        <div class="card">
          <button id="togglePasswordBoxBtn" class="password-trigger-btn" type="button">
            <span>Password</span>
            <span id="passwordArrowIcon">&#9662;</span>
          </button>

          <div id="passwordOptionsPanel" class="quick-add-box hidden">
            <div class="field">
              <label class="field-label" for="currentPasswordInput">Current Password</label>
              <input type="password" id="currentPasswordInput" placeholder="Enter current password" autocomplete="current-password" />
            </div>
            <div class="field">
              <label class="field-label" for="newPasswordInput">New Password</label>
              <input type="password" id="newPasswordInput" placeholder="Enter new password (min 6 chars)" autocomplete="new-password" />
            </div>
            <div class="field">
              <label class="field-label" for="confirmPasswordInput">Enter Password Again</label>
              <input type="password" id="confirmPasswordInput" placeholder="Enter password again" autocomplete="new-password" />
            </div>
            <div id="changePasswordFeedback" class="post-sub" style="margin-bottom:8px; color:var(--accent);"></div>
            <button id="changePasswordBtn" class="primary-btn" type="button">Save Password</button>
          </div>
        </div>

        <div class="card">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
            <div>
              <div class="settings-section-title">Switch Accounts</div>
              <p class="post-sub">Switch between saved accounts on this device or add another account.</p>
            </div>
            <button id="toggleAddAccountBtn" class="pill-btn" style="margin-top:0;" type="button">+ Add Account</button>
          </div>

          <div id="savedAccountsList" style="margin-top:8px;"></div>

          <div id="addAccountPanel" class="quick-add-box hidden">
            <h4 style="margin-bottom:8px;">Add or Sign In to Another Account</h4>
            <div class="field"><input type="text" id="extraAccUsername" placeholder="Username" /></div>
            <div class="field"><input type="password" id="extraAccPassword" placeholder="Password" /></div>
            <div class="field"><input type="text" id="extraAccName" placeholder="Full Name (optional for existing account)" /></div>
            <div style="display:flex; gap:8px;">
              <button id="extraAccLoginBtn" class="pill-btn" style="margin-top:0; flex:1;" type="button">Sign In &amp; Switch</button>
              <button id="extraAccCreateBtn" class="pill-btn active" style="margin-top:0; flex:1;" type="button">Create &amp; Switch</button>
            </div>
            <div id="extraAccStatus" class="post-sub" style="margin-top:8px; color:var(--accent);"></div>
          </div>
        </div>
      </section>
    </main>

    <div id="sharePostModal" class="overlay-backdrop hidden">
      <div class="share-sheet">
        <div class="share-handle"></div>
        <div class="share-header">
          <h4>Send Post</h4>
          <button id="closeShareModalBtn" class="pill-btn" style="margin-top:0; padding:4px 12px;" type="button">Close</button>
        </div>
        <input id="shareSearchInput" type="search" placeholder="Search following to send..." />
        <div id="shareRecipientsGrid" class="share-grid"></div>
        <div id="shareModalFeedback" class="post-sub" style="color:var(--accent);"></div>
      </div>
    </div>

    <div id="postPreviewModal" class="overlay-backdrop center hidden">
      <div id="postPreviewBody" class="preview-modal-card"></div>
    </div>
  </div>

  <script>
    (() => {
      const API_BASE = window.location.origin.startsWith("http")
        ? window.location.origin
        : "https://nidishwar.pythonanywhere.com";

      const THEME_KEY = "forge_theme_mode";
      const SAVED_ACCOUNTS_KEY = "forge_saved_accounts";
      const CLIENT_RESET_KEY = "forge_client_reset_v5";
      const MAX_CHARS = 500;
      const MAX_IMAGE_SIZE = 25 * 1024 * 1024;
      const MAX_CHAT_IMAGE_SIZE = 10 * 1024 * 1024;
      const MAX_VIDEO_SIZE = 250 * 1024 * 1024;

      try {
        if (!localStorage.getItem(CLIENT_RESET_KEY)) {
          localStorage.removeItem(SAVED_ACCOUNTS_KEY);
          localStorage.setItem(CLIENT_RESET_KEY, "1");
        }
      } catch {}

      const $ = (id) => document.getElementById(id);

      let currentTabId = "homeTab";
      let previousTabId = "homeTab";
      let currentUser = null;
      let cachedPosts = [];
      let cachedUsers = [];
      let cachedAllMessages = [];
      let activeChatPeer = "";
      let selectedChatPhotoFile = null;
      let selectedChatPhotoUrl = "";
      let selectedMediaType = "";
      let selectedMediaFile = null;
      let selectedMediaPreviewUrl = "";
      let activeFollowTabMode = "followers";
      let pendingSharePostId = null;
      let heartbeatTimer = null;
      const likedPostIds = new Set();
      const openCommentPostIds = new Set();

      const esc = (s) => String(s || "")
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

      const splitCsv = (val) => String(val || "").split(",").map((s) => s.trim()).filter(Boolean);
      const isFollowed = (peer) => Boolean(peer && currentUser && Array.isArray(currentUser.connections) && currentUser.connections.includes(peer));
      const isOnline = (uname) => {
        if (!uname) return false;
        if (currentUser && currentUser.username === uname) return true;
        const found = cachedUsers.find((u) => u.username === uname);
        return Boolean(found && found.is_online);
      };

      const resolveMedia = (url) => !url ? "" : (/^(https?:|data:|blob:)/i.test(url) ? url : (url.startsWith("/") ? API_BASE + url : API_BASE + "/" + url));
      const defaultAvatar = (label) => "https://ui-avatars.com/api/?name=" + encodeURIComponent(label || "Member") + "&background=6d28d9&color=fff&size=160";
      const avatarOf = (obj) => (obj && obj.avatar) ? resolveMedia(obj.avatar) : defaultAvatar((obj && (obj.name || obj.author || obj.username)) || "Member");

      async function api(path, options) {
        const res = await fetch(API_BASE + path, options);
        const text = await res.text();
        let data;
        try { data = JSON.parse(text); } catch { data = { message: text }; }
        return { ok: res.ok, status: res.status, data };
      }

      async function sendHeartbeat() {
        if (!currentUser || !currentUser.username) return;
        await api("/presence", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: currentUser.username }),
        }).catch(() => {});
      }

      function clearChatPhoto() {
        selectedChatPhotoFile = null;
        if (selectedChatPhotoUrl && selectedChatPhotoUrl.startsWith("blob:")) URL.revokeObjectURL(selectedChatPhotoUrl);
        selectedChatPhotoUrl = "";
        if ($("chatPhotoInput")) $("chatPhotoInput").value = "";
        if ($("chatPhotoThumb")) $("chatPhotoThumb").src = "";
        if ($("chatPhotoName")) $("chatPhotoName").textContent = "Photo";
        if ($("chatPhotoPreviewBar")) $("chatPhotoPreviewBar").classList.add("hidden");
      }

      function syncComposer() {
        const raw = $("newPostText") ? $("newPostText").value : "";
        const len = raw.length;
        if ($("postCharCounter")) {
          $("postCharCounter").textContent = `${len} / ${MAX_CHARS}`;
          $("postCharCounter").classList.toggle("limit-near", len >= MAX_CHARS - 40);
        }
        if ($("publishPostBtn")) $("publishPostBtn").disabled = !(raw.trim().length > 0 && len <= MAX_CHARS);
      }

      function clearPostMedia() {
        selectedMediaType = "";
        selectedMediaFile = null;
        if (selectedMediaPreviewUrl && selectedMediaPreviewUrl.startsWith("blob:")) URL.revokeObjectURL(selectedMediaPreviewUrl);
        selectedMediaPreviewUrl = "";
        if ($("postImageInput")) $("postImageInput").value = "";
        if ($("postVideoInput")) $("postVideoInput").value = "";
        if ($("postImagePreviewImg")) { $("postImagePreviewImg").src = ""; $("postImagePreviewImg").classList.add("hidden"); }
        if ($("postVideoPreviewPlayer")) {
          $("postVideoPreviewPlayer").pause();
          $("postVideoPreviewPlayer").removeAttribute("src");
          $("postVideoPreviewPlayer").load();
          $("postVideoPreviewPlayer").classList.add("hidden");
        }
        if ($("postMediaPreviewBox")) $("postMediaPreviewBox").classList.add("hidden");
      }

      function updateHeaderControls() {
        const isHome = currentTabId === "homeTab";
        if ($("openPostBtn")) $("openPostBtn").classList.toggle("hidden", !isHome);
        if ($("openNotificationsBtn")) $("openNotificationsBtn").classList.toggle("hidden", !isHome);
        if (!isHome) {
          if ($("createPostBox")) $("createPostBox").classList.add("hidden");
          if ($("postFeedback")) $("postFeedback").textContent = "";
          if ($("homeNotificationsBox")) $("homeNotificationsBox").classList.add("hidden");
          if ($("openNotificationsBtn")) $("openNotificationsBtn").classList.remove("active");
        }
      }

      function validateSignup(f) {
        if (!["Student", "Job Creator"].includes(f.role)) return "Please select whether you are a Student or Job Creator.";
        if (!/^[A-Za-z0-9_]{3,24}$/.test(f.username)) return "Username must be 3-24 characters (letters, numbers, or underscore only).";
        if (!f.password || f.password.length < 6) return "Password must be at least 6 characters long.";
        if (!f.name || f.name.length < 2) return "Please enter your proper full name (at least 2 characters).";
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(f.email)) return "Please enter a valid email address.";
        if (!f.course) return "Please select a course.";
        if (!f.year) return "Please select your year (1, 2, 3, or 4).";
        if (!f.bio || f.bio.length < 5) return "Please enter a proper bio (at least 5 characters).";
        if (!Array.isArray(f.skills) || !f.skills.length) return "Please enter at least one skill.";
        return "";
      }

      function applyTheme(mode) {
        const clean = mode === "dark" ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", clean);
        try { localStorage.setItem(THEME_KEY, clean); } catch {}
        if ($("lightModeBtn")) $("lightModeBtn").classList.toggle("active", clean === "light");
        if ($("darkModeBtn")) $("darkModeBtn").classList.toggle("active", clean === "dark");
      }

      function getSavedAccounts() {
        try {
          const parsed = JSON.parse(localStorage.getItem(SAVED_ACCOUNTS_KEY) || "[]");
          return Array.isArray(parsed) ? parsed : [];
        } catch { return []; }
      }

      function saveAccount(userObj, password, prevUsername) {
        if (!userObj || !userObj.username) return;
        const list = getSavedAccounts();
        const existing = list.find((a) => a.username === userObj.username || (prevUsername && a.username === prevUsername));
        const entry = {
          username: userObj.username,
          name: userObj.name || userObj.username,
          role: userObj.role || (existing ? existing.role : "Student"),
          course: userObj.course || userObj.programme || "",
          password: password !== undefined ? password : (existing ? existing.password : ""),
        };
        const next = [entry].concat(list.filter((a) => a.username !== userObj.username && a.username !== prevUsername));
        try { localStorage.setItem(SAVED_ACCOUNTS_KEY, JSON.stringify(next)); } catch {}
      }

      function renderSavedAccounts() {
        const box = $("savedAccountsList");
        if (!box) return;
        const accounts = getSavedAccounts();
        const active = currentUser ? currentUser.username : "";
        if (!accounts.length) {
          box.innerHTML = `<div class="account-row"><span class="post-sub">No saved accounts yet.</span></div>`;
          return;
        }
        box.innerHTML = accounts.map((acc) => `
          <div class="account-row">
            <div>
              <strong>${esc(acc.name || acc.username)}</strong>
              <span class="post-sub">@${esc(acc.username)}</span>
              ${(acc.role || acc.course) ? `<div class="post-sub">${esc([acc.role, acc.course].filter(Boolean).join(" · "))}</div>` : ""}
            </div>
            ${acc.username === active
              ? `<span class="active-pill">Active</span>`
              : `<button class="connect-btn switch-acc-btn" data-username="${esc(acc.username)}" type="button">Switch</button>`}
          </div>
        `).join("");

        box.querySelectorAll(".switch-acc-btn").forEach((btn) => {
          btn.addEventListener("click", async () => {
            const target = btn.dataset.username;
            const found = getSavedAccounts().find((a) => a.username === target);
            btn.disabled = true;
            btn.textContent = "Switching...";
            try {
              const { ok, data } = await api("/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: target, password: (found && found.password) || "" }),
              });
              if (ok && data && !data.error) {
                currentUser = data.profile || { username: target, name: target };
                saveAccount(currentUser, found ? found.password : "");
                renderProfileHeader();
                renderSavedAccounts();
                activateTab("moreTab");
              }
            } finally {
              btn.disabled = false;
              btn.textContent = "Switch";
            }
          });
        });
      }

      function showAuthMsg(msg, isError) {
        const el = $("authMessage");
        if (!el) return;
        if (!msg) { el.textContent = ""; el.className = "status-msg"; return; }
        el.textContent = msg;
        el.className = isError ? "status-msg visible error" : "status-msg visible";
      }

      function switchAuthTab(mode) {
        const isLogin = mode === "login";
        $("showLoginBtn").classList.toggle("active", isLogin);
        $("showSignupBtn").classList.toggle("active", !isLogin);
        $("loginForm").classList.toggle("hidden", !isLogin);
        $("signupForm").classList.toggle("hidden", isLogin);
        showAuthMsg("", false);
      }

      function activateTab(tabId) {
        if (currentTabId !== tabId && currentTabId !== "memberProfileTab") {
          previousTabId = currentTabId;
        }
        currentTabId = tabId;
        document.querySelectorAll(".tab-view").forEach((sec) => sec.classList.toggle("hidden", sec.id !== tabId));
        const navId = (tabId === "settingsTab" || tabId === "followTab" || tabId === "memberProfileTab") ? "moreTab" : tabId;
        document.querySelectorAll(".nav-btn").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === navId));
        updateHeaderControls();

        if (tabId === "homeTab") loadPosts();
        if (tabId === "jobsTab") loadJobs();
        if (tabId === "networkTab") loadFeedTab();
        if (tabId === "notificationsTab") loadChatsList();
        if (tabId === "followTab") renderFollowTab(activeFollowTabMode);
        if (tabId === "moreTab") loadProfile();
        if (tabId === "settingsTab") {
          if ($("changePasswordFeedback")) $("changePasswordFeedback").textContent = "";
          renderSavedAccounts();
        }
      }

      const getUserKey = () => ((currentUser && (currentUser.username || currentUser.name)) || "").trim().toLowerCase();

      function isOwnPost(p) {
        if (!currentUser || !p) return false;
        const u = (currentUser.username || "").trim().toLowerCase();
        const n = (currentUser.name || "").trim().toLowerCase();
        const pu = (p.author_username || "").trim().toLowerCase();
        const pa = (p.author || "").trim().toLowerCase();
        return Boolean((u && (pu === u || pa === u)) || (n && pa === n));
      }

      function isReposted(p) {
        const list = Array.isArray(p && p.reposted_by) ? p.reposted_by : [];
        const k = getUserKey();
        const u = ((currentUser && currentUser.username) || "").trim().toLowerCase();
        const n = ((currentUser && currentUser.name) || "").trim().toLowerCase();
        return list.some((entry) => {
          const v = String(entry || "").trim().toLowerCase();
          return (k && v === k) || (u && v === u) || (n && v === n);
        });
      }

      function renderProfileHeader() {
        const u = currentUser || {};
        const name = u.name || u.username || "Member";
        const avatar = avatarOf(u);
        if ($("welcomePromo")) $("welcomePromo").textContent = name.toUpperCase() + ", a complete profile helps the right opportunities find you";
        if ($("topAvatar")) $("topAvatar").src = avatar;
        if ($("profileDetailAvatar")) $("profileDetailAvatar").src = avatar;
        if ($("moreProfileName")) $("moreProfileName").textContent = name;

        const conns = Array.isArray(u.connections) ? u.connections : [];
        if ($("followersCount")) $("followersCount").textContent = u.followers_count !== undefined ? Number(u.followers_count) : 0;
        if ($("followingCount")) $("followingCount").textContent = u.following_count !== undefined ? Number(u.following_count) : conns.length;

        if ($("moreProfileMeta")) {
          $("moreProfileMeta").textContent = [
            u.username ? "@" + u.username : "",
            u.role || "",
            u.email || "",
            u.course || u.programme || "",
            u.year ? "Year " + u.year : ""
          ].filter(Boolean).join(" · ");
        }
        if ($("moreProfileBio")) $("moreProfileBio").textContent = u.bio || "Building projects and connecting on Forge.";
        if ($("moreProfileSkills")) {
          const skills = Array.isArray(u.skills) ? u.skills : [];
          $("moreProfileSkills").innerHTML = skills.length
            ? skills.map((s) => `<span class="skill-tag">${esc(s)}</span>`).join("")
            : `<span class="post-sub">No skills listed yet</span>`;
        }
      }

      async function openMemberProfile(targetUsername) {
        if (!targetUsername) return;
        if (currentUser && targetUsername === currentUser.username) {
          activateTab("moreTab");
          return;
        }
        await refreshUserStats();
        await fetchPosts();
        const userObj = cachedUsers.find((u) => u.username === targetUsername);
        if (!userObj) return;

        const conn = isFollowed(targetUsername);
        const online = isOnline(targetUsername);
        const memberPosts = cachedPosts.filter((p) => (p.author_username || "").trim().toLowerCase() === targetUsername.trim().toLowerCase());
        const skills = Array.isArray(userObj.skills) ? userObj.skills : [];

        if ($("memberProfileContent")) {
          $("memberProfileContent").innerHTML = `
            <div class="profile-header-row">
              <div class="profile-dp-wrap">
                <img class="profile-dp" src="${avatarOf(userObj)}" alt="${esc(userObj.name || userObj.username)}" />
                ${online ? `<span class="online-badge">ON ONLINE</span>` : ""}
              </div>
              <div class="profile-stats-side">
                <div class="stat-box"><div class="stat-num">${Number(userObj.followers_count || 0)}</div><div class="stat-label">Followers</div></div>
                <div class="stat-box"><div class="stat-num">${Number(userObj.following_count || (userObj.connections || []).length || 0)}</div><div class="stat-label">Following</div></div>
                <div class="stat-box"><div class="stat-num">${memberPosts.length}</div><div class="stat-label">Posts</div></div>
              </div>
            </div>
            <div style="margin-top:12px;">
              <h4>${esc(userObj.name || userObj.username)}</h4>
              <div class="post-sub" style="margin-top:2px;">${esc(["@" + userObj.username, userObj.role, userObj.course || userObj.programme, userObj.year ? "Year " + userObj.year : ""].filter(Boolean).join(" · "))}</div>
              <p class="post-text">${esc(userObj.bio || "Forge Member")}</p>
              <div style="margin-top:8px;">${skills.map((s) => `<span class="skill-tag">${esc(s)}</span>`).join("")}</div>
            </div>
            <div style="margin-top:14px; display:flex; gap:8px;">
              <button id="memberProfileFollowBtn" class="connect-btn ${conn ? "connected" : ""}" type="button">${conn ? "Unfollow" : "Follow"}</button>
              ${conn ? `<button id="memberProfileMsgBtn" class="connect-btn" type="button">Message</button>` : ""}
            </div>
          `;

          if ($("memberProfileFollowBtn")) {
            $("memberProfileFollowBtn").addEventListener("click", async () => {
              const res = await api("/connect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  username: currentUser ? currentUser.username : "",
                  actor_name: (currentUser && (currentUser.name || currentUser.username)) || "Member",
                  target_username: targetUsername,
                }),
              });
              if (res.ok && currentUser) {
                currentUser.connections = res.data.connections || [];
                currentUser.following_count = currentUser.connections.length;
                renderProfileHeader();
                openMemberProfile(targetUsername);
              }
            });
          }

          if ($("memberProfileMsgBtn")) {
            $("memberProfileMsgBtn").addEventListener("click", () => {
              activeChatPeer = targetUsername;
              activateTab("notificationsTab");
              openChat(targetUsername);
            });
          }
        }
        activateTab("memberProfileTab");
      }

      function renderFollowTab(mode) {
        const box = $("followTabListItems");
        if (!box || !$("followTabHeading")) return;
        activeFollowTabMode = mode === "following" ? "following" : "followers";
        const me = currentUser ? currentUser.username : "";
        const myConns = (currentUser && Array.isArray(currentUser.connections)) ? currentUser.connections : [];

        if ($("switchToFollowersListBtn")) $("switchToFollowersListBtn").classList.toggle("active", activeFollowTabMode === "followers");
        if ($("switchToFollowingListBtn")) $("switchToFollowingListBtn").classList.toggle("active", activeFollowTabMode === "following");

        const usersList = activeFollowTabMode === "followers"
          ? (($("followTabHeading").textContent = "Followers List"), cachedUsers.filter((u) => u.username && u.username !== me && Array.isArray(u.connections) && u.connections.includes(me)))
          : (($("followTabHeading").textContent = "Following List"), cachedUsers.filter((u) => u.username && myConns.includes(u.username)));

        if (!usersList.length) {
          box.innerHTML = `<div class="list-item"><span class="post-sub">${activeFollowTabMode === "followers" ? "No followers yet." : "Not following anyone yet."}</span></div>`;
          return;
        }

        box.innerHTML = usersList.map((u) => {
          const f = myConns.includes(u.username);
          const online = Boolean(u.is_online);
          return `
            <div class="list-item">
              <div style="display:flex; align-items:center; gap:10px; min-width:0;">
                <div class="peer-avatar-col open-member-trigger" data-user="${esc(u.username)}">
                  <img class="chat-thread-avatar" style="width:40px; height:40px;" src="${avatarOf(u)}" alt="${esc(u.name || u.username)}" />
                  ${online ? `<span class="online-badge">ON ONLINE</span>` : ""}
                </div>
                <div class="clickable-member open-member-trigger" data-user="${esc(u.username)}" style="min-width:0;">
                  <strong>${esc(u.name || u.username)}</strong> <span class="post-sub">@${esc(u.username)}</span>
                  <div class="post-sub">${esc([u.role, u.course || u.programme, u.year ? "Year " + u.year : "", u.bio].filter(Boolean).join(" · ") || "Forge Member")}</div>
                </div>
              </div>
              <button class="connect-btn follow-tab-toggle-btn ${f ? "connected" : ""}" data-target="${esc(u.username)}" type="button">${f ? "Unfollow" : "Follow"}</button>
            </div>
          `;
        }).join("");

        box.querySelectorAll(".open-member-trigger").forEach((el) => el.addEventListener("click", () => openMemberProfile(el.dataset.user)));
        box.querySelectorAll(".follow-tab-toggle-btn").forEach((btn) => {
          btn.addEventListener("click", async () => {
            const res = await api("/connect", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                username: currentUser ? currentUser.username : "",
                actor_name: (currentUser && (currentUser.name || currentUser.username)) || "Member",
                target_username: btn.dataset.target,
              }),
            });
            if (res.ok) {
              if (currentUser) {
                currentUser.connections = res.data.connections || [];
                currentUser.following_count = currentUser.connections.length;
              }
              await refreshUserStats();
              renderProfileHeader();
              renderFollowTab(activeFollowTabMode);
            }
          });
        });
      }

      function findPostAvatar(p) {
        if (p && p.author_avatar) return resolveMedia(p.author_avatar);
        if (p && p.author_username) {
          const found = cachedUsers.find((u) => u.username === p.author_username);
          if (found && found.avatar) return resolveMedia(found.avatar);
        }
        if (currentUser && isOwnPost(p) && currentUser.avatar) return resolveMedia(currentUser.avatar);
        return defaultAvatar(p ? p.author : "User");
      }

      function openFullPostPreview(pid) {
        const p = cachedPosts.find((item) => String(item.id) === String(pid));
        const modal = $("postPreviewModal");
        const body = $("postPreviewBody");
        if (!p || !modal || !body) return;
        const imgUrl = Array.isArray(p.images) && p.images.length ? resolveMedia(p.images[0]) : "";
        const vidUrl = Array.isArray(p.videos) && p.videos.length ? resolveMedia(p.videos[0]) : "";
        const commentsArr = Array.isArray(p.comments_list) ? p.comments_list : [];

        body.innerHTML = `
          <div class="preview-top-bar">
            <div class="post-author-wrap open-member-trigger" data-user="${esc(p.author_username || "")}" style="cursor:pointer;">
              <img class="post-avatar" src="${findPostAvatar(p)}" alt="${esc(p.author)}" />
              <div>
                <div class="post-name">${esc(p.author)}</div>
                <div class="post-sub">${esc(p.headline || "Forge Member")} · ${esc(p.time || "Just now")}</div>
              </div>
            </div>
            <button id="closePostPreviewBtn" class="pill-btn" style="margin-top:0; padding:5px 14px;" type="button">Close</button>
          </div>
          <p class="post-text" style="font-size:15px;">${esc(p.text)}</p>
          ${imgUrl ? `<img class="preview-full-img" src="${imgUrl}" alt="Full post image" />` : ""}
          ${vidUrl ? `<video class="preview-full-vid" src="${vidUrl}" controls playsinline autoplay></video>` : ""}
          <div class="post-stats" style="margin-top:12px;">
            <span><strong>${Number(p.likes || 0)}</strong> Likes</span>
            <span><strong>${commentsArr.length}</strong> Comments · <strong>${Number(p.reposts || 0)}</strong> Reposts</span>
          </div>
          <div style="margin-top:10px; display:flex; flex-direction:column; gap:6px;">
            ${commentsArr.length
              ? commentsArr.map((c) => `<div class="comment-item"><span class="comment-author">${esc(c.author || "Member")}</span><span>${esc(c.text || "")}</span></div>`).join("")
              : `<div class="post-sub">No comments yet.</div>`}
          </div>
        `;
        modal.classList.remove("hidden");
        if ($("closePostPreviewBtn")) {
          $("closePostPreviewBtn").addEventListener("click", () => {
            const v = body.querySelector("video");
            if (v) v.pause();
            modal.classList.add("hidden");
          });
        }
        body.querySelectorAll(".open-member-trigger").forEach((el) => {
          el.addEventListener("click", () => {
            modal.classList.add("hidden");
            if (el.dataset.user) openMemberProfile(el.dataset.user);
          });
        });
      }

      function openSharePostModal(pid) {
        pendingSharePostId = String(pid);
        const modal = $("sharePostModal");
        if (!modal) return;
        if ($("shareSearchInput")) $("shareSearchInput").value = "";
        if ($("shareModalFeedback")) $("shareModalFeedback").textContent = "";
        renderShareRecipients("");
        modal.classList.remove("hidden");
        if ($("shareSearchInput")) $("shareSearchInput").focus();
      }

      function renderShareRecipients(filterText) {
        const grid = $("shareRecipientsGrid");
        if (!grid) return;
        const me = currentUser ? currentUser.username : "";
        const myConns = (currentUser && Array.isArray(currentUser.connections)) ? currentUser.connections : [];
        const q = (filterText || "").trim().toLowerCase();

        const targets = cachedUsers.filter((u) => {
          if (!u.username || u.username === me || !myConns.includes(u.username)) return false;
          if (!q) return true;
          return `${u.name || ""} ${u.username || ""}`.toLowerCase().includes(q);
        });

        if (!targets.length) {
          grid.innerHTML = `<div class="post-sub" style="grid-column:1/-1; padding:12px 0;">Follow members to send posts directly in chat.</div>`;
          return;
        }

        grid.innerHTML = targets.map((u) => `
          <button class="share-person-btn" data-share-user="${esc(u.username)}" type="button">
            <img class="share-person-avatar" src="${avatarOf(u)}" alt="${esc(u.name || u.username)}" />
            <span class="share-person-name">${esc(u.name || u.username)}</span>
          </button>
        `).join("");

        grid.querySelectorAll(".share-person-btn").forEach((btn) => {
          btn.addEventListener("click", async () => {
            const receiver = btn.dataset.shareUser;
            const post = cachedPosts.find((item) => String(item.id) === String(pendingSharePostId));
            if (!receiver || !post || !currentUser || !currentUser.username) return;
            btn.disabled = true;
            if ($("shareModalFeedback")) $("shareModalFeedback").textContent = `Sending to @${receiver}...`;
            const shareText = `Shared post by ${post.author || "Member"}: "${post.text || ""}"`;
            const res = await api("/messages", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                sender: currentUser.username,
                sender_name: currentUser.name || currentUser.username,
                receiver,
                text: shareText,
                image: Array.isArray(post.images) && post.images.length ? post.images[0] : "",
              }),
            });
            if (res.ok) {
              if ($("shareModalFeedback")) $("shareModalFeedback").textContent = `Sent to ${receiver}!`;
              setTimeout(() => {
                if ($("sharePostModal")) $("sharePostModal").classList.add("hidden");
                activeChatPeer = receiver;
                activateTab("notificationsTab");
                openChat(receiver);
              }, 350);
            } else {
              btn.disabled = false;
              if ($("shareModalFeedback")) $("shareModalFeedback").textContent = "Could not send post.";
            }
          });
        });
      }

      function renderPosts(postsArr, container, allowDelete, emptyMsg) {
        if (!container) return;
        if (!Array.isArray(postsArr) || !postsArr.length) {
          container.innerHTML = `<div class="card"><p class="post-sub">${esc(emptyMsg || "No posts yet.")}</p></div>`;
          return;
        }

        container.innerHTML = postsArr.map((p) => {
          const pid = String(p.id);
          const liked = likedPostIds.has(pid);
          const reposted = isReposted(p);
          const canDel = Boolean(allowDelete && isOwnPost(p));
          const commentsArr = Array.isArray(p.comments_list) ? p.comments_list : [];
          const commentsCnt = commentsArr.length > 0 ? commentsArr.length : Number(p.comments || 0);
          const repostsCnt = Array.isArray(p.reposted_by) && p.reposted_by.length > 0 ? p.reposted_by.length : Number(p.reposts || 0);
          const commentsOpen = openCommentPostIds.has(pid);
          const imgUrl = Array.isArray(p.images) && p.images.length ? resolveMedia(p.images[0]) : "";
          const vidUrl = Array.isArray(p.videos) && p.videos.length ? resolveMedia(p.videos[0]) : "";

          const commentsHtml = commentsArr.length
            ? commentsArr.map((c) => `<div class="comment-item"><span class="comment-author">${esc(c.author || "Member")}</span><span>${esc(c.text || "")}</span></div>`).join("")
            : `<div class="post-sub" style="margin-bottom:6px;">No comments yet.</div>`;

          return `
            <div class="card post-card-clickable" id="post-card-${p.id}" data-preview-id="${p.id}">
              <div class="post-head">
                <div class="post-author-wrap">
                  <div class="post-author-trigger open-member-trigger" data-user="${esc(p.author_username || "")}">
                    <img class="post-avatar" src="${findPostAvatar(p)}" alt="${esc(p.author)}" />
                    <div>
                      <div class="post-name">${esc(p.author)} · <span style="font-weight:400;color:var(--text-secondary);">${esc(p.badge || "1st")}</span></div>
                      <div class="post-sub">${esc(p.headline || "Forge Member")}<br/>${esc(p.time || "Just now")}</div>
                    </div>
                  </div>
                </div>
                ${canDel ? `<button class="delete-post-btn" data-delete-id="${p.id}" type="button" title="Delete post"><span>Delete</span></button>` : ""}
              </div>
              <p class="post-text">${esc(p.text)}</p>
              ${imgUrl ? `<img class="post-image-media" src="${imgUrl}" alt="Post attachment" />` : ""}
              ${vidUrl ? `<video class="post-video-media" src="${vidUrl}" controls playsinline preload="metadata"></video>` : ""}
              <div class="post-stats">
                <span>Likes: <strong class="like-num-${p.id}">${Number(p.likes || 0)}</strong></span>
                <span><strong class="comment-num-${p.id}">${commentsCnt}</strong> Comments · <strong class="repost-num-${p.id}">${repostsCnt}</strong> Reposts</span>
              </div>
              <div class="post-actions">
                <button class="post-action-btn like-btn ${liked ? "liked" : ""}" data-post-id="${p.id}" type="button">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="${liked ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path>
                  </svg>
                  <span>${liked ? "Liked" : "Like"}</span>
                </button>
                <button class="post-action-btn comment-toggle-btn" data-post-id="${p.id}" type="button">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                  </svg>
                  <span>Comment</span>
                </button>
                <button class="post-action-btn repost-btn ${reposted ? "reposted" : ""}" data-post-id="${p.id}" type="button">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <polyline points="17 1 21 5 17 9"></polyline>
                    <path d="M3 11V9a4 4 0 0 1 4-4h14"></path>
                    <polyline points="7 23 3 19 7 15"></polyline>
                    <path d="M21 13v2a4 4 0 0 1-4 4H3"></path>
                  </svg>
                  <span>${reposted ? "Undo Repost" : "Repost"}</span>
                </button>
                <button class="post-action-btn send-post-btn" data-post-id="${p.id}" type="button">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <line x1="22" y1="2" x2="11" y2="13"></line>
                    <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                  </svg>
                  <span>Send</span>
                </button>
              </div>
              <div class="comments-panel ${commentsOpen ? "" : "hidden"}" id="comments-panel-${p.id}">
                <div class="comments-list" id="comments-list-${p.id}">${commentsHtml}</div>
                <div class="comment-form-row">
                  <input type="text" class="comment-input-${p.id}" placeholder="Write a comment..." />
                  <button class="comment-submit-btn" data-comment-submit="${p.id}" type="button">Post</button>
                </div>
              </div>
            </div>
          `;
        }).join("");

        container.querySelectorAll(".post-card-clickable").forEach((card) => {
          card.addEventListener("click", (e) => {
            if (e.target.closest("button, input, textarea, select, video, .comments-panel, .open-member-trigger")) return;
            openFullPostPreview(card.dataset.previewId);
          });
        });

        container.querySelectorAll(".open-member-trigger").forEach((el) => {
          el.addEventListener("click", (e) => {
            e.stopPropagation();
            if (el.dataset.user) openMemberProfile(el.dataset.user);
          });
        });

        container.querySelectorAll(".like-btn").forEach((btn) => {
          btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const pid = String(btn.dataset.postId);
            const already = likedPostIds.has(pid);
            const res = await api(`/posts/${pid}/like`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                action: already ? "unlike" : "like",
                actor_name: (currentUser && (currentUser.name || currentUser.username)) || "Member",
                actor_username: (currentUser && currentUser.username) || "",
              }),
            });
            if (res.ok && res.data && res.data.likes !== undefined) {
              already ? likedPostIds.delete(pid) : likedPostIds.add(pid);
              const nowLiked = likedPostIds.has(pid);
              document.querySelectorAll(`.like-num-${pid}`).forEach((el) => { el.textContent = res.data.likes; });
              document.querySelectorAll(`.like-btn[data-post-id="${pid}"]`).forEach((b) => {
                b.classList.toggle("liked", nowLiked);
                const sp = b.querySelector("span");
                if (sp) sp.textContent = nowLiked ? "Liked" : "Like";
                const svg = b.querySelector("svg");
                if (svg) svg.setAttribute("fill", nowLiked ? "currentColor" : "none");
              });
            }
          });
        });

        container.querySelectorAll(".comment-toggle-btn").forEach((btn) => {
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const pid = String(btn.dataset.postId);
            openCommentPostIds.has(pid) ? openCommentPostIds.delete(pid) : openCommentPostIds.add(pid);
            const isOpen = openCommentPostIds.has(pid);
            document.querySelectorAll(`#comments-panel-${pid}`).forEach((panel) => {
              panel.classList.toggle("hidden", !isOpen);
              if (isOpen) { const inp = panel.querySelector("input"); if (inp) inp.focus(); }
            });
          });
        });

        async function submitComment(pid) {
          const panel = container.querySelector(`#comments-panel-${pid}`);
          const inp = panel ? panel.querySelector("input") : null;
          const text = inp ? inp.value.trim() : "";
          if (!text) return;
          const res = await api(`/posts/${pid}/comment`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              text,
              author: (currentUser && (currentUser.name || currentUser.username)) || "Member",
              actor_username: (currentUser && currentUser.username) || "",
            }),
          });
          if (res.ok && res.data && res.data.post) {
            cachedPosts = cachedPosts.map((item) => (String(item.id) === String(pid) ? res.data.post : item));
            openCommentPostIds.add(String(pid));
            if (inp) inp.value = "";
            if (!$("homeTab").classList.contains("hidden")) renderPosts(getHomePosts(), $("feedList"), false, "Follow members to see their posts on Home.");
            if (!$("networkTab").classList.contains("hidden")) renderPosts(cachedPosts, $("videoAndUnknownFeedList"), false, "No posts available in Feed yet.");
            if (!$("moreTab").classList.contains("hidden")) renderPosts(cachedPosts.filter(isOwnPost), $("myPostsList"), true, "You haven't created any posts yet.");
          }
        }

        container.querySelectorAll(".comment-submit-btn").forEach((btn) => btn.addEventListener("click", (e) => { e.stopPropagation(); submitComment(btn.dataset.commentSubmit); }));
        container.querySelectorAll(".comments-panel input").forEach((inp) => inp.addEventListener("keydown", (e) => {
          if (e.key === "Enter") { e.preventDefault(); submitComment(inp.className.replace("comment-input-", "")); }
        }));

        container.querySelectorAll(".repost-btn").forEach((btn) => {
          btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const pid = String(btn.dataset.postId);
            const actor = (currentUser && (currentUser.username || currentUser.name)) || "Member";
            const actorName = (currentUser && (currentUser.name || currentUser.username)) || "Member";
            const res = await api(`/posts/${pid}/repost`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ username: actor, actor_name: actorName }),
            });
            if (res.ok && res.data && res.data.post) {
              const updated = res.data.post;
              cachedPosts = cachedPosts.map((item) => (String(item.id) === pid ? updated : item));
              const nowRep = isReposted(updated);
              document.querySelectorAll(`.repost-num-${pid}`).forEach((el) => { el.textContent = Number(updated.reposts || 0); });
              document.querySelectorAll(`.repost-btn[data-post-id="${pid}"]`).forEach((b) => {
                b.classList.toggle("reposted", nowRep);
                const sp = b.querySelector("span");
                if (sp) sp.textContent = nowRep ? "Undo Repost" : "Repost";
              });
              if ($("profileRepostsSection") && !$("profileRepostsSection").classList.contains("hidden")) renderReposts();
            }
          });
        });

        container.querySelectorAll(".send-post-btn").forEach((btn) => {
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            openSharePostModal(btn.dataset.postId);
          });
        });

        container.querySelectorAll(".delete-post-btn").forEach((btn) => {
          btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const pid = btn.dataset.deleteId;
            btn.disabled = true;
            try {
              const res = await api(`/posts/${pid}`, { method: "DELETE" });
              if (res.ok) {
                cachedPosts = cachedPosts.filter((item) => String(item.id) !== String(pid));
                likedPostIds.delete(String(pid));
                await loadProfile();
              } else btn.disabled = false;
            } catch { btn.disabled = false; }
          });
        });
      }

      function getHomePosts() {
        const followingSet = new Set(
          (currentUser && Array.isArray(currentUser.connections))
            ? currentUser.connections.map((u) => String(u).trim().toLowerCase())
            : []
        );
        return cachedPosts.filter((p) => {
          if (isOwnPost(p)) return true;
          const authorU = String(p.author_username || "").trim().toLowerCase();
          return Boolean(authorU && followingSet.has(authorU));
        });
      }

      async function fetchPosts() {
        const { ok, data } = await api("/posts");
        if (ok && data && Array.isArray(data.posts)) cachedPosts = data.posts;
        return cachedPosts;
      }

      async function refreshUserStats() {
        if (!currentUser || !currentUser.username) return;
        const { ok, data } = await api("/users");
        if (ok && data && Array.isArray(data.users)) {
          cachedUsers = data.users;
          const fresh = data.users.find((u) => u.username === currentUser.username);
          if (fresh) currentUser = Object.assign({}, currentUser, fresh);
        }
      }

      function renderReposts() {
        if ($("profileRepostsList")) renderPosts(cachedPosts.filter(isReposted), $("profileRepostsList"), false, "No reposted posts yet.");
      }

      async function loadFeedTab() {
        await refreshUserStats();
        await fetchPosts();
        renderPosts(cachedPosts, $("videoAndUnknownFeedList"), false, "No posts available in Feed yet.");
      }

      async function loadProfile() {
        await refreshUserStats();
        renderProfileHeader();
        const posts = await fetchPosts();
        const mine = posts.filter(isOwnPost);
        if ($("userPostsCount")) $("userPostsCount").textContent = mine.length;
        renderPosts(mine, $("myPostsList"), true, "You haven't created any posts yet.");
        if ($("profileRepostsSection") && !$("profileRepostsSection").classList.contains("hidden")) renderReposts();
      }

      function enterApp(userObj, passwordUsed) {
        currentUser = userObj || {};
        saveAccount(currentUser, passwordUsed);
        renderProfileHeader();
        $("authSection").classList.add("hidden");
        $("mainApp").classList.remove("hidden");
        showAuthMsg("", false);
        sendHeartbeat();
        if (heartbeatTimer) clearInterval(heartbeatTimer);
        heartbeatTimer = setInterval(sendHeartbeat, 60000);
        activateTab("homeTab");
      }

      async function loadPosts() {
        await refreshUserStats();
        await fetchPosts();
        renderPosts(getHomePosts(), $("feedList"), false, "Follow members to see their posts on Home.");
      }

      async function loadJobs() {
        if (!$("jobsList")) return;
        const { ok, data } = await api("/jobs");
        const jobs = ok && data && Array.isArray(data.jobs) ? data.jobs : [];
        if (!jobs.length) {
          $("jobsList").innerHTML = `<div class="list-item"><span class="post-sub">No jobs yet. Click Create New Job to add one.</span></div>`;
          return;
        }
        $("jobsList").innerHTML = jobs.map((j) => `
          <div class="list-item">
            <div>
              <strong>${esc(j.title)}</strong>
              <div class="post-sub">${esc([j.company, j.location].filter(Boolean).join(" · "))}</div>
              <div style="margin-top:4px;">${(j.skills || []).map((s) => `<span class="skill-tag">${esc(s)}</span>`).join("")}</div>
            </div>
            <button class="connect-btn" type="button">Apply</button>
          </div>
        `).join("");
      }

      async function runHomeSearch(query) {
        const box = $("homeSearchResultsBox");
        const list = $("homeSearchResultsList");
        if (!box || !list) return;
        const q = (query || "").trim();
        if (!q) { box.classList.add("hidden"); list.innerHTML = ""; return; }
        box.classList.remove("hidden");
        const { ok, data } = await api(`/users?q=${encodeURIComponent(q)}`);
        const users = ok && data && Array.isArray(data.users) ? data.users : [];
        const activeU = currentUser ? currentUser.username : "";
        const myConns = (currentUser && Array.isArray(currentUser.connections)) ? currentUser.connections : [];
        const others = users.filter((u) => u.username !== activeU);
        if (!others.length) {
          list.innerHTML = `<div class="list-item"><span class="post-sub">No matching accounts found for "${esc(q)}".</span></div>`;
          return;
        }
        list.innerHTML = others.map((u) => {
          const conn = myConns.includes(u.username);
          const skillsHtml = Array.isArray(u.skills) && u.skills.length
            ? `<div style="margin-top:4px;">${u.skills.map((s) => `<span class="skill-tag">${esc(s)}</span>`).join("")}</div>`
            : "";
          return `
            <div class="list-item">
              <div style="display:flex; align-items:center; gap:10px; min-width:0;">
                <div class="peer-avatar-col open-member-trigger" data-user="${esc(u.username)}">
                  <img class="chat-thread-avatar" style="width:40px; height:40px;" src="${avatarOf(u)}" alt="${esc(u.name || u.username)}" />
                  ${u.is_online ? `<span class="online-badge">ON ONLINE</span>` : ""}
                </div>
                <div class="clickable-member open-member-trigger" data-user="${esc(u.username)}" style="min-width:0;">
                  <strong>${esc(u.name || u.username)}</strong> <span class="post-sub">@${esc(u.username)}</span>
                  <div class="post-sub">${esc([u.role, u.course || u.programme, u.year ? "Year " + u.year : "", u.bio].filter(Boolean).join(" · ") || "Forge Member")}</div>
                  ${skillsHtml}
                </div>
              </div>
              <div style="display:flex; gap:6px; flex-shrink:0;">
                ${conn ? `<button class="connect-btn message-peer-btn" data-peer="${esc(u.username)}" type="button">Message</button>` : ""}
                <button class="connect-btn follow-toggle-btn ${conn ? "connected" : ""}" data-target="${esc(u.username)}" type="button">${conn ? "Unfollow" : "Follow"}</button>
              </div>
            </div>
          `;
        }).join("");

        list.querySelectorAll(".open-member-trigger").forEach((el) => el.addEventListener("click", () => openMemberProfile(el.dataset.user)));
        list.querySelectorAll(".message-peer-btn").forEach((btn) => btn.addEventListener("click", () => {
          const peer = btn.dataset.peer || "";
          if (peer && isFollowed(peer)) { activeChatPeer = peer; activateTab("notificationsTab"); openChat(activeChatPeer); }
        }));

        list.querySelectorAll(".follow-toggle-btn").forEach((btn) => btn.addEventListener("click", async () => {
          const res = await api("/connect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              username: currentUser ? currentUser.username : "",
              actor_name: (currentUser && (currentUser.name || currentUser.username)) || "Member",
              target_username: btn.dataset.target,
            }),
          });
          if (res.ok && currentUser) {
            currentUser.connections = res.data.connections || [];
            currentUser.following_count = currentUser.connections.length;
            renderProfileHeader();
            await runHomeSearch($("homeTopSearchInput") ? $("homeTopSearchInput").value : "");
          }
        }));
      }

      function latestPreview(me, peer) {
        if (!me || !peer || !Array.isArray(cachedAllMessages)) return "";
        const thread = cachedAllMessages.filter((m) => (m.sender === me && m.receiver === peer) || (m.sender === peer && m.receiver === me));
        if (!thread.length) return "Tap to start a chat";
        const last = thread[thread.length - 1];
        return (last.sender === me ? "You: " : "") + (last.text || (last.image ? "Sent a photo" : ""));
      }

      async function loadChatsList() {
        const listView = $("chatListView");
        if (!listView) return;
        await refreshUserStats();
        const me = currentUser ? currentUser.username : "";
        const msgsRes = await api(me ? `/messages?user1=${encodeURIComponent(me)}` : "/messages");
        if (msgsRes.ok && msgsRes.data && Array.isArray(msgsRes.data.messages)) cachedAllMessages = msgsRes.data.messages;

        const following = (currentUser && Array.isArray(currentUser.connections)) ? currentUser.connections : [];
        const peers = cachedUsers.filter((u) => u.username && u.username !== me && following.includes(u.username));

        if ($("chatConversationView")) $("chatConversationView").classList.add("hidden");
        listView.classList.remove("hidden");

        if (!me || !peers.length) {
          activeChatPeer = "";
          listView.innerHTML = `<div class="chat-blank-space"></div>`;
          return;
        }

        listView.innerHTML = peers.map((p) => {
          const disp = p.name || p.username;
          const prev = latestPreview(me, p.username);
          const sub = prev !== "Tap to start a chat" ? prev : ([p.course || p.programme, p.year ? "Year " + p.year : ""].filter(Boolean).join(" · ") || "Start a conversation");
          return `
            <div class="chat-thread-row" data-chat-peer="${esc(p.username)}">
              <div class="peer-avatar-col open-member-trigger" data-user="${esc(p.username)}">
                <img class="chat-thread-avatar" src="${avatarOf(p)}" alt="${esc(disp)}" />
                ${p.is_online ? `<span class="online-badge">ON ONLINE</span>` : ""}
              </div>
              <div class="chat-thread-info">
                <div class="chat-thread-name"><span>${esc(disp)}</span><span class="chat-thread-handle">@${esc(p.username)}</span></div>
                <div class="chat-thread-preview">${esc(sub)}</div>
              </div>
              <div class="chat-thread-chevron">&rsaquo;</div>
            </div>
          `;
        }).join("");

        listView.querySelectorAll(".open-member-trigger").forEach((el) => {
          el.addEventListener("click", (e) => { e.stopPropagation(); openMemberProfile(el.dataset.user); });
        });
        listView.querySelectorAll(".chat-thread-row").forEach((row) => row.addEventListener("click", () => {
          const peer = row.dataset.chatPeer || "";
          if (peer && isFollowed(peer)) openChat(peer);
        }));
      }

      async function openChat(peerUsername) {
        if (!peerUsername || !isFollowed(peerUsername)) {
          activeChatPeer = "";
          await loadChatsList();
          return;
        }
        activeChatPeer = peerUsername;
        if ($("chatListView")) $("chatListView").classList.add("hidden");
        if ($("chatConversationView")) $("chatConversationView").classList.remove("hidden");
        if ($("chatFeedback")) $("chatFeedback").textContent = "";

        const peer = cachedUsers.find((u) => u.username === peerUsername) || { username: peerUsername, name: peerUsername };
        if ($("activeChatAvatar")) $("activeChatAvatar").src = avatarOf(peer);
        if ($("activeChatTitle")) $("activeChatTitle").textContent = peer.name || peer.username;
        if ($("activeChatSub")) $("activeChatSub").textContent = `@${peer.username}` + (peer.is_online ? " · ON ONLINE" : "") + (peer.course ? ` · ${peer.course}` : "");
        if ($("chatMessageInput")) $("chatMessageInput").focus();
        await renderMessages();
      }

      async function renderMessages() {
        const box = $("chatMessagesBox");
        if (!box) return;
        const me = currentUser ? currentUser.username : "";
        const peer = activeChatPeer;
        if (!me || !peer || !isFollowed(peer)) { box.innerHTML = ""; return; }
        const { ok, data } = await api(`/messages?user1=${encodeURIComponent(me)}&user2=${encodeURIComponent(peer)}`);
        const msgs = ok && data && Array.isArray(data.messages) ? data.messages : [];
        if (!msgs.length) { box.innerHTML = ""; return; }
        box.innerHTML = msgs.map((m) => {
          const sent = m.sender === me;
          const imgUrl = m.image ? resolveMedia(m.image) : "";
          return `
            <div class="msg-bubble ${sent ? "sent" : "received"}">
              ${m.text ? `<div>${esc(m.text)}</div>` : ""}
              ${imgUrl ? `<img class="chat-msg-image" src="${imgUrl}" alt="Shared photo" />` : ""}
              <div class="msg-meta">${sent ? "You" : "@" + esc(m.sender)} · ${esc(m.time || "")}</div>
            </div>
          `;
        }).join("");
        box.scrollTop = box.scrollHeight;
      }

      async function sendChat() {
        const inp = $("chatMessageInput");
        if (!inp) return;
        const text = inp.value.trim();
        const sender = currentUser ? currentUser.username : "";
        const senderName = (currentUser && (currentUser.name || currentUser.username)) || sender;
        const receiver = activeChatPeer;
        if (!sender || !receiver || !isFollowed(receiver)) return;
        if (!text && !selectedChatPhotoFile) {
          if ($("chatFeedback")) $("chatFeedback").textContent = "Type a message or pick a photo (max 10 MB).";
          return;
        }
        if (selectedChatPhotoFile && selectedChatPhotoFile.size > MAX_CHAT_IMAGE_SIZE) {
          if ($("chatFeedback")) $("chatFeedback").textContent = "Photo size limit is 10 MB.";
          return;
        }
        if ($("sendChatBtn")) $("sendChatBtn").disabled = true;
        if ($("chatFeedback")) $("chatFeedback").textContent = selectedChatPhotoFile ? "Uploading photo & sending..." : "Sending...";
        try {
          let ok = false, data = null;
          if (selectedChatPhotoFile) {
            const fd = new FormData();
            fd.append("sender", sender); fd.append("sender_name", senderName);
            fd.append("receiver", receiver); fd.append("text", text); fd.append("image", selectedChatPhotoFile);
            const r = await fetch(API_BASE + "/messages", { method: "POST", body: fd });
            ok = r.ok; data = await r.json().catch(() => ({}));
          } else {
            const res = await api("/messages", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ sender, sender_name: senderName, receiver, text }),
            });
            ok = res.ok; data = res.data;
          }
          if (ok && data && !data.error) {
            inp.value = "";
            clearChatPhoto();
            if ($("chatFeedback")) $("chatFeedback").textContent = "";
            if (data.data) cachedAllMessages.push(data.data);
            await renderMessages();
          } else if ($("chatFeedback")) $("chatFeedback").textContent = (data && data.error) || "Could not send message.";
        } catch (err) {
          if ($("chatFeedback")) $("chatFeedback").textContent = "Network error: " + err.message;
        } finally {
          if ($("sendChatBtn")) $("sendChatBtn").disabled = false;
        }
      }

      if ($("activeChatPeerTrigger")) {
        $("activeChatPeerTrigger").addEventListener("click", () => {
          if (activeChatPeer) openMemberProfile(activeChatPeer);
        });
      }
      if ($("backFromMemberProfileBtn")) {
        $("backFromMemberProfileBtn").addEventListener("click", () => activateTab(previousTabId || "homeTab"));
      }
      if ($("closeShareModalBtn")) {
        $("closeShareModalBtn").addEventListener("click", () => $("sharePostModal").classList.add("hidden"));
      }
      if ($("shareSearchInput")) {
        $("shareSearchInput").addEventListener("input", () => renderShareRecipients($("shareSearchInput").value));
      }
      if ($("postPreviewModal")) {
        $("postPreviewModal").addEventListener("click", (e) => {
          if (e.target === $("postPreviewModal")) {
            const v = $("postPreviewModal").querySelector("video");
            if (v) v.pause();
            $("postPreviewModal").classList.add("hidden");
          }
        });
      }

      if ($("openEditProfileBtn")) {
        $("openEditProfileBtn").addEventListener("click", () => {
          const box = $("editProfileBox");
          if (!box || !currentUser) return;
          const willOpen = box.classList.contains("hidden");
          box.classList.toggle("hidden", !willOpen);
          $("openEditProfileBtn").classList.toggle("active", willOpen);
          if ($("editProfileFeedback")) $("editProfileFeedback").textContent = "";
          if (willOpen) {
            if ($("editRoleSelect")) $("editRoleSelect").value = ["Student", "Job Creator"].includes(currentUser.role) ? currentUser.role : "Student";
            if ($("editNameInput")) $("editNameInput").value = currentUser.name || "";
            if ($("editUsernameInput")) $("editUsernameInput").value = currentUser.username || "";
            if ($("editBioInput")) $("editBioInput").value = currentUser.bio || "";
            if ($("editSkillsInput")) $("editSkillsInput").value = Array.isArray(currentUser.skills) ? currentUser.skills.join(", ") : "";
            if ($("editNameInput")) $("editNameInput").focus();
          }
        });
      }

      if ($("cancelProfileEditBtn")) {
        $("cancelProfileEditBtn").addEventListener("click", () => {
          if ($("editProfileBox")) $("editProfileBox").classList.add("hidden");
          if ($("openEditProfileBtn")) $("openEditProfileBtn").classList.remove("active");
          if ($("editProfileFeedback")) $("editProfileFeedback").textContent = "";
        });
      }

      if ($("saveProfileEditBtn")) {
        $("saveProfileEditBtn").addEventListener("click", async () => {
          if (!currentUser || !currentUser.username) return;
          const role = $("editRoleSelect") ? $("editRoleSelect").value : "Student";
          const name = ($("editNameInput") ? $("editNameInput").value : "").trim();
          const username = ($("editUsernameInput") ? $("editUsernameInput").value : "").trim();
          const bio = ($("editBioInput") ? $("editBioInput").value : "").trim();
          const skills = splitCsv($("editSkillsInput") ? $("editSkillsInput").value : "");
          if (name.length < 2) { if ($("editProfileFeedback")) $("editProfileFeedback").textContent = "Name must be at least 2 characters."; return; }
          if (!/^[A-Za-z0-9_]{3,24}$/.test(username)) { if ($("editProfileFeedback")) $("editProfileFeedback").textContent = "Username must be 3-24 chars (letters, numbers, or _)."; return; }

          $("saveProfileEditBtn").disabled = true;
          if ($("editProfileFeedback")) $("editProfileFeedback").textContent = "Saving profile...";
          const prevU = currentUser.username;
          const payload = { current_username: prevU, role, name, username };
          if (bio.length >= 5) payload.bio = bio;
          if (skills.length) payload.skills = skills;

          try {
            const { ok, data } = await api("/profile/update", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            });
            if (ok && data && data.profile) {
              currentUser = Object.assign({}, currentUser, data.profile);
              saveAccount(currentUser, undefined, prevU);
              renderProfileHeader();
              renderSavedAccounts();
              if ($("editProfileBox")) $("editProfileBox").classList.add("hidden");
              if ($("openEditProfileBtn")) $("openEditProfileBtn").classList.remove("active");
              if ($("editProfileFeedback")) $("editProfileFeedback").textContent = "";
              await loadProfile();
            } else if ($("editProfileFeedback")) $("editProfileFeedback").textContent = (data && data.error) || "Could not update profile.";
          } catch (err) {
            if ($("editProfileFeedback")) $("editProfileFeedback").textContent = "Network error: " + err.message;
          } finally {
            $("saveProfileEditBtn").disabled = false;
          }
        });
      }

      async function uploadProfilePic(file) {
        if (!file || !currentUser || !currentUser.username) return;
        if (!file.type || !file.type.startsWith("image/")) { if ($("profilePicFeedback")) $("profilePicFeedback").textContent = "Please choose an image file from gallery."; return; }
        if (file.size > MAX_IMAGE_SIZE) { if ($("profilePicFeedback")) $("profilePicFeedback").textContent = "Profile image must be under 25 MB."; return; }
        if ($("profilePicFeedback")) $("profilePicFeedback").textContent = "Uploading profile photo...";
        const fd = new FormData();
        fd.append("username", currentUser.username);
        fd.append("avatar", file);
        try {
          const res = await fetch(API_BASE + "/profile/avatar", { method: "POST", body: fd });
          const data = await res.json();
          if (res.ok && data && data.profile) {
            currentUser = Object.assign({}, currentUser, data.profile);
            renderProfileHeader();
            if ($("profilePicFeedback")) $("profilePicFeedback").textContent = "Profile picture updated.";
          } else if ($("profilePicFeedback")) $("profilePicFeedback").textContent = (data && data.error) || "Could not update profile picture.";
        } catch (err) {
          if ($("profilePicFeedback")) $("profilePicFeedback").textContent = "Upload error: " + err.message;
        }
      }

      if ($("changeProfilePicBtn") && $("profilePicGalleryInput")) $("changeProfilePicBtn").addEventListener("click", () => $("profilePicGalleryInput").click());
      if ($("profileDetailAvatar") && $("profilePicGalleryInput")) $("profileDetailAvatar").addEventListener("click", () => $("profilePicGalleryInput").click());
      if ($("profilePicGalleryInput")) $("profilePicGalleryInput").addEventListener("change", () => {
        const f = $("profilePicGalleryInput").files && $("profilePicGalleryInput").files[0];
        $("profilePicGalleryInput").value = "";
        uploadProfilePic(f);
      });

      $("showLoginBtn").addEventListener("click", () => switchAuthTab("login"));
      $("showSignupBtn").addEventListener("click", () => switchAuthTab("signup"));

      $("loginForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const username = $("loginUsername").value.trim();
        const password = $("loginPassword").value || "";
        if (!username || !password) return showAuthMsg("Please enter both username and password.", true);
        showAuthMsg("Signing in...", false);
        try {
          const { ok, data } = await api("/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
          if (ok && data && !data.error) enterApp(data.profile || { username, name: username }, password);
          else showAuthMsg((data && (data.error || data.message)) || "Could not sign in.", true);
        } catch (err) { showAuthMsg("Network error: " + err.message, true); }
      });

      $("signupForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = {
          role: $("role").value.trim(),
          username: $("username").value.trim(),
          password: $("password").value || "",
          name: $("name").value.trim(),
          email: $("email").value.trim(),
          course: $("course").value.trim(),
          programme: $("course").value.trim(),
          year: $("year").value.trim(),
          bio: $("bio").value.trim(),
          skills: splitCsv($("skills").value),
        };
        const errMsg = validateSignup(payload);
        if (errMsg) return showAuthMsg(errMsg, true);
        showAuthMsg("Creating account...", false);
        try {
          const { ok, data } = await api("/signup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
          if (ok && data && !data.error) enterApp(data.profile || payload, payload.password);
          else showAuthMsg((data && (data.error || data.message)) || "Could not create account.", true);
        } catch (err) { showAuthMsg("Network error: " + err.message, true); }
      });

      document.querySelectorAll(".nav-btn").forEach((btn) => btn.addEventListener("click", () => activateTab(btn.dataset.tab)));
      if ($("topAvatar")) $("topAvatar").addEventListener("click", () => activateTab("moreTab"));
      if ($("topBrandTitle")) $("topBrandTitle").addEventListener("click", () => activateTab("homeTab"));
      if ($("statFollowersBox")) $("statFollowersBox").addEventListener("click", async () => { activeFollowTabMode = "followers"; await refreshUserStats(); activateTab("followTab"); });
      if ($("statFollowingBox")) $("statFollowingBox").addEventListener("click", async () => { activeFollowTabMode = "following"; await refreshUserStats(); activateTab("followTab"); });
      if ($("statPostsBox")) $("statPostsBox").addEventListener("click", () => activateTab("moreTab"));
      if ($("switchToFollowersListBtn")) $("switchToFollowersListBtn").addEventListener("click", () => renderFollowTab("followers"));
      if ($("switchToFollowingListBtn")) $("switchToFollowingListBtn").addEventListener("click", () => renderFollowTab("following"));
      if ($("backFromFollowTabBtn")) $("backFromFollowTabBtn").addEventListener("click", () => activateTab("moreTab"));
      if ($("openProfileSettingsBtn")) $("openProfileSettingsBtn").addEventListener("click", () => activateTab("settingsTab"));
      if ($("backToProfileBtn")) $("backToProfileBtn").addEventListener("click", () => activateTab("moreTab"));

      if ($("openNotificationsBtn")) $("openNotificationsBtn").addEventListener("click", async () => {
        if (currentTabId !== "homeTab" || !$("homeNotificationsBox")) return;
        const willOpen = $("homeNotificationsBox").classList.contains("hidden");
        $("homeNotificationsBox").classList.toggle("hidden", !willOpen);
        $("openNotificationsBtn").classList.toggle("active", willOpen);
        if (willOpen && $("homeNotificationsList")) {
          const { ok, data } = await api("/notifications");
          const items = ok && data && Array.isArray(data.notifications) ? data.notifications : [];
          $("homeNotificationsList").innerHTML = items.length
            ? items.map((n) => `<div class="list-item"><strong style="font-size:14px;">${esc(typeof n === "string" ? n : (n.name || n.username || ""))}</strong></div>`).join("")
            : `<div class="list-item"><span class="post-sub">No notifications yet.</span></div>`;
        }
      });

      if ($("closeHomeNotificationsBtn")) $("closeHomeNotificationsBtn").addEventListener("click", () => {
        if ($("homeNotificationsBox")) $("homeNotificationsBox").classList.add("hidden");
        if ($("openNotificationsBtn")) $("openNotificationsBtn").classList.remove("active");
      });

      function openComposer(toggleOnly) {
        if (currentTabId !== "homeTab" || !$("createPostBox")) return;
        if ($("homeNotificationsBox")) $("homeNotificationsBox").classList.add("hidden");
        if ($("openNotificationsBtn")) $("openNotificationsBtn").classList.remove("active");
        const willOpen = toggleOnly ? $("createPostBox").classList.contains("hidden") : true;
        $("createPostBox").classList.toggle("hidden", !willOpen);
        if ($("postFeedback")) $("postFeedback").textContent = "";
        syncComposer();
        if (willOpen && $("newPostText")) $("newPostText").focus();
      }

      if ($("getStartedBtn")) $("getStartedBtn").addEventListener("click", () => openComposer(false));
      if ($("openPostBtn")) $("openPostBtn").addEventListener("click", () => openComposer(true));
      if ($("newPostText")) $("newPostText").addEventListener("input", () => { if ($("postFeedback")) $("postFeedback").textContent = ""; syncComposer(); });

      if ($("postImageInput")) $("postImageInput").addEventListener("change", () => {
        const file = $("postImageInput").files && $("postImageInput").files[0];
        if (!file) return;
        if (!file.type || !file.type.startsWith("image/")) { if ($("postFeedback")) $("postFeedback").textContent = "Please choose a valid image file."; $("postImageInput").value = ""; return; }
        if (file.size > MAX_IMAGE_SIZE) { if ($("postFeedback")) $("postFeedback").textContent = "Image size limit is 25 MB."; $("postImageInput").value = ""; return; }
        clearPostMedia();
        selectedMediaType = "image"; selectedMediaFile = file; selectedMediaPreviewUrl = URL.createObjectURL(file);
        if ($("postImagePreviewImg")) { $("postImagePreviewImg").src = selectedMediaPreviewUrl; $("postImagePreviewImg").classList.remove("hidden"); }
        if ($("postMediaPreviewBox")) $("postMediaPreviewBox").classList.remove("hidden");
        if ($("postFeedback")) $("postFeedback").textContent = "";
      });

      if ($("postVideoInput")) $("postVideoInput").addEventListener("change", () => {
        const file = $("postVideoInput").files && $("postVideoInput").files[0];
        if (!file) return;
        if (!file.type || !file.type.startsWith("video/")) { if ($("postFeedback")) $("postFeedback").textContent = "Please choose a valid video file."; $("postVideoInput").value = ""; return; }
        if (file.size > MAX_VIDEO_SIZE) { if ($("postFeedback")) $("postFeedback").textContent = "Video size limit is 250 MB."; $("postVideoInput").value = ""; return; }
        clearPostMedia();
        selectedMediaType = "video"; selectedMediaFile = file; selectedMediaPreviewUrl = URL.createObjectURL(file);
        if ($("postVideoPreviewPlayer")) { $("postVideoPreviewPlayer").src = selectedMediaPreviewUrl; $("postVideoPreviewPlayer").classList.remove("hidden"); }
        if ($("postMediaPreviewBox")) $("postMediaPreviewBox").classList.remove("hidden");
        if ($("postFeedback")) $("postFeedback").textContent = "";
      });

      if ($("removePostMediaBtn")) $("removePostMediaBtn").addEventListener("click", clearPostMedia);

      if ($("publishPostBtn") && $("newPostText")) $("publishPostBtn").addEventListener("click", async () => {
        const raw = $("newPostText").value;
        const text = raw.trim();
        if (!text) { if ($("postFeedback")) $("postFeedback").textContent = "Empty post is not allowed. Write something first."; syncComposer(); return; }
        if (raw.length > MAX_CHARS) { if ($("postFeedback")) $("postFeedback").textContent = `Post limit is ${MAX_CHARS} characters.`; syncComposer(); return; }

        $("publishPostBtn").disabled = true;
        if ($("postFeedback")) $("postFeedback").textContent = selectedMediaFile ? "Uploading media & publishing post..." : "Publishing post...";
        const coursePart = currentUser && (currentUser.course || currentUser.programme) ? (currentUser.course || currentUser.programme) : "";
        const yearPart = currentUser && currentUser.year ? "Year " + currentUser.year : "";
        const author = (currentUser && (currentUser.name || currentUser.username)) || "Member";
        const author_username = (currentUser && currentUser.username) || "";
        const headline = [coursePart, yearPart].filter(Boolean).join(" · ") || "Forge Member";

        try {
          let resOk = false, resData = null;
          if (selectedMediaFile) {
            const fd = new FormData();
            fd.append("text", text); fd.append("author", author); fd.append("author_username", author_username); fd.append("headline", headline);
            fd.append(selectedMediaType === "video" ? "video" : "image", selectedMediaFile);
            const r = await fetch(API_BASE + "/posts", { method: "POST", body: fd });
            resOk = r.ok; resData = await r.json().catch(() => ({}));
          } else {
            const r = await api("/posts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, author, author_username, headline }) });
            resOk = r.ok; resData = r.data;
          }
          if (resOk && resData && !resData.error) {
            if (resData.post) cachedPosts = [resData.post].concat(cachedPosts.filter((x) => x.id !== resData.post.id));
            $("newPostText").value = "";
            clearPostMedia();
            syncComposer();
            if ($("postFeedback")) $("postFeedback").textContent = "";
            $("createPostBox").classList.add("hidden");
            activateTab("homeTab");
          } else {
            if ($("postFeedback")) $("postFeedback").textContent = (resData && resData.error) || "Could not publish post.";
            syncComposer();
          }
        } catch (err) {
          if ($("postFeedback")) $("postFeedback").textContent = "Network error: " + err.message;
          syncComposer();
        }
      });

      if ($("toggleCreateJobBtn") && $("createJobBox")) $("toggleCreateJobBtn").addEventListener("click", () => {
        $("createJobBox").classList.toggle("hidden");
        if ($("jobFeedback")) $("jobFeedback").textContent = "";
        if (!$("createJobBox").classList.contains("hidden") && $("jobTitleInput")) $("jobTitleInput").focus();
      });

      if ($("publishJobBtn")) $("publishJobBtn").addEventListener("click", async () => {
        const title = ($("jobTitleInput") ? $("jobTitleInput").value : "").trim();
        if (!title) { if ($("jobFeedback")) $("jobFeedback").textContent = "Please enter a job title."; return; }
        $("publishJobBtn").disabled = true;
        if ($("jobFeedback")) $("jobFeedback").textContent = "Creating job...";
        try {
          const { ok, data } = await api("/jobs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              title,
              company: ($("jobCompanyInput") ? $("jobCompanyInput").value : "").trim(),
              location: ($("jobLocationInput") ? $("jobLocationInput").value : "").trim(),
              skills: splitCsv($("jobSkillsInput") ? $("jobSkillsInput").value : ""),
            }),
          });
          if (ok && data && !data.error) {
            ["jobTitleInput", "jobCompanyInput", "jobLocationInput", "jobSkillsInput"].forEach((id) => { if ($(id)) $(id).value = ""; });
            if ($("jobFeedback")) $("jobFeedback").textContent = "";
            if ($("createJobBox")) $("createJobBox").classList.add("hidden");
            await loadJobs();
          } else if ($("jobFeedback")) $("jobFeedback").textContent = (data && data.error) || "Could not create job.";
        } catch (err) { if ($("jobFeedback")) $("jobFeedback").textContent = "Network error: " + err.message; }
        finally { $("publishJobBtn").disabled = false; }
      });

      if ($("homeTopSearchInput")) $("homeTopSearchInput").addEventListener("input", () => runHomeSearch($("homeTopSearchInput").value));
      if ($("clearHomeSearchBtn")) $("clearHomeSearchBtn").addEventListener("click", () => { if ($("homeTopSearchInput")) $("homeTopSearchInput").value = ""; runHomeSearch(""); });

      if ($("chatPhotoPickBtn") && $("chatPhotoInput")) $("chatPhotoPickBtn").addEventListener("click", () => $("chatPhotoInput").click());
      if ($("removeChatPhotoBtn")) $("removeChatPhotoBtn").addEventListener("click", () => { clearChatPhoto(); if ($("chatFeedback")) $("chatFeedback").textContent = ""; });
      if ($("chatPhotoInput")) $("chatPhotoInput").addEventListener("change", () => {
        const file = $("chatPhotoInput").files && $("chatPhotoInput").files[0];
        if (!file) return;
        if (!file.type || !file.type.startsWith("image/")) { if ($("chatFeedback")) $("chatFeedback").textContent = "Please choose a valid photo file."; $("chatPhotoInput").value = ""; return; }
        if (file.size > MAX_CHAT_IMAGE_SIZE) { if ($("chatFeedback")) $("chatFeedback").textContent = "Photo size limit is 10 MB."; $("chatPhotoInput").value = ""; return; }
        clearChatPhoto();
        selectedChatPhotoFile = file; selectedChatPhotoUrl = URL.createObjectURL(file);
        if ($("chatPhotoThumb")) $("chatPhotoThumb").src = selectedChatPhotoUrl;
        if ($("chatPhotoName")) $("chatPhotoName").textContent = file.name || "Photo";
        if ($("chatPhotoPreviewBar")) $("chatPhotoPreviewBar").classList.remove("hidden");
        if ($("chatFeedback")) $("chatFeedback").textContent = "";
      });
      if ($("backToChatsListBtn")) $("backToChatsListBtn").addEventListener("click", () => { clearChatPhoto(); loadChatsList(); });
      if ($("sendChatBtn")) $("sendChatBtn").addEventListener("click", sendChat);
      if ($("chatMessageInput")) $("chatMessageInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); sendChat(); } });

      if ($("profileRepostsBtn") && $("profileRepostsSection")) $("profileRepostsBtn").addEventListener("click", async () => {
        const willOpen = $("profileRepostsSection").classList.contains("hidden");
        $("profileRepostsSection").classList.toggle("hidden", !willOpen);
        $("profileRepostsBtn").classList.toggle("active", willOpen);
        if (willOpen) { await fetchPosts(); renderReposts(); }
      });
      if ($("profileMyPostsBtn") && $("profileMyPostsSection")) $("profileMyPostsBtn").addEventListener("click", async () => {
        const willShow = $("profileMyPostsSection").classList.contains("hidden");
        $("profileMyPostsSection").classList.toggle("hidden", !willShow);
        $("profileMyPostsBtn").classList.toggle("active", willShow);
        if (willShow) await loadProfile();
      });
      if ($("closeProfileRepostsBtn") && $("profileRepostsSection")) $("closeProfileRepostsBtn").addEventListener("click", () => {
        $("profileRepostsSection").classList.add("hidden");
        if ($("profileRepostsBtn")) $("profileRepostsBtn").classList.remove("active");
      });

      if ($("lightModeBtn")) $("lightModeBtn").addEventListener("click", () => applyTheme("light"));
      if ($("darkModeBtn")) $("darkModeBtn").addEventListener("click", () => applyTheme("dark"));

      if ($("togglePasswordBoxBtn") && $("passwordOptionsPanel")) $("togglePasswordBoxBtn").addEventListener("click", () => {
        const willOpen = $("passwordOptionsPanel").classList.contains("hidden");
        $("passwordOptionsPanel").classList.toggle("hidden", !willOpen);
        if ($("passwordArrowIcon")) $("passwordArrowIcon").innerHTML = willOpen ? "&#9652;" : "&#9662;";
        if ($("changePasswordFeedback")) $("changePasswordFeedback").textContent = "";
        if (willOpen && $("currentPasswordInput")) $("currentPasswordInput").focus();
      });

      if ($("changePasswordBtn")) $("changePasswordBtn").addEventListener("click", async () => {
        if (!currentUser || !currentUser.username) return;
        const cur = $("currentPasswordInput") ? $("currentPasswordInput").value : "";
        const next = $("newPasswordInput") ? $("newPasswordInput").value : "";
        const conf = $("confirmPasswordInput") ? $("confirmPasswordInput").value : "";
        if (!cur) { if ($("changePasswordFeedback")) $("changePasswordFeedback").textContent = "Please enter your current password."; return; }
        if (next.length < 6) { if ($("changePasswordFeedback")) $("changePasswordFeedback").textContent = "New password must be at least 6 characters."; return; }
        if (next !== conf) { if ($("changePasswordFeedback")) $("changePasswordFeedback").textContent = "New password and re-entered password do not match."; return; }
        $("changePasswordBtn").disabled = true;
        if ($("changePasswordFeedback")) $("changePasswordFeedback").textContent = "Updating password...";
        try {
          const { ok, data } = await api("/profile/password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: currentUser.username, current_password: cur, new_password: next, confirm_password: conf }),
          });
          if (ok && data && !data.error) {
            saveAccount(currentUser, next);
            ["currentPasswordInput", "newPasswordInput", "confirmPasswordInput"].forEach((id) => { if ($(id)) $(id).value = ""; });
            if ($("changePasswordFeedback")) $("changePasswordFeedback").textContent = "Password updated successfully.";
          } else if ($("changePasswordFeedback")) $("changePasswordFeedback").textContent = (data && data.error) || "Could not change password.";
        } catch (err) { if ($("changePasswordFeedback")) $("changePasswordFeedback").textContent = "Network error: " + err.message; }
        finally { $("changePasswordBtn").disabled = false; }
      });

      if ($("toggleAddAccountBtn") && $("addAccountPanel")) $("toggleAddAccountBtn").addEventListener("click", () => {
        $("addAccountPanel").classList.toggle("hidden");
        if ($("extraAccStatus")) $("extraAccStatus").textContent = "";
      });

      if ($("extraAccLoginBtn")) $("extraAccLoginBtn").addEventListener("click", async () => {
        const username = ($("extraAccUsername") ? $("extraAccUsername").value : "").trim();
        const password = $("extraAccPassword") ? $("extraAccPassword").value : "";
        if (!username || !password) { if ($("extraAccStatus")) $("extraAccStatus").textContent = "Enter both username and password to sign in."; return; }
        if ($("extraAccStatus")) $("extraAccStatus").textContent = "Signing in...";
        const { ok, data } = await api("/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
        if (ok && data && !data.error) {
          currentUser = data.profile || { username, name: username };
          saveAccount(currentUser, password);
          ["extraAccUsername", "extraAccPassword", "extraAccName"].forEach((id) => { if ($(id)) $(id).value = ""; });
          if ($("extraAccStatus")) $("extraAccStatus").textContent = "Switched to @" + username;
          renderProfileHeader(); renderSavedAccounts(); activateTab("moreTab");
        } else if ($("extraAccStatus")) $("extraAccStatus").textContent = (data && data.error) || "Sign in failed.";
      });

      if ($("extraAccCreateBtn")) $("extraAccCreateBtn").addEventListener("click", async () => {
        const username = ($("extraAccUsername") ? $("extraAccUsername").value : "").trim();
        const password = $("extraAccPassword") ? $("extraAccPassword").value : "";
        const name = ($("extraAccName") ? $("extraAccName").value : "").trim();
        if (!/^[A-Za-z0-9_]{3,24}$/.test(username) || password.length < 6 || name.length < 2) {
          if ($("extraAccStatus")) $("extraAccStatus").textContent = "Enter valid username (3+ chars), password (6+ chars), and full name (2+ chars).";
          return;
        }
        if ($("extraAccStatus")) $("extraAccStatus").textContent = "Creating account...";
        const { ok, data } = await api("/signup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: "Student", username, password, name, email: username + "@forge.local", course: "B.Tech", programme: "B.Tech", year: "1", bio: "Forge member", skills: ["General"] }),
        });
        if (ok && data && !data.error) {
          currentUser = data.profile || { username, name };
          saveAccount(currentUser, password);
          ["extraAccUsername", "extraAccPassword", "extraAccName"].forEach((id) => { if ($(id)) $(id).value = ""; });
          if ($("extraAccStatus")) $("extraAccStatus").textContent = "Created and switched to @" + username;
          renderProfileHeader(); renderSavedAccounts(); activateTab("moreTab");
        } else if ($("extraAccStatus")) $("extraAccStatus").textContent = (data && data.error) || "Could not create account.";
      });

      if ($("logoutBtn")) $("logoutBtn").addEventListener("click", async () => {
        if (heartbeatTimer) clearInterval(heartbeatTimer);
        await api("/logout", { method: "POST" }).catch(() => {});
        currentUser = null;
        $("mainApp").classList.add("hidden");
        $("authSection").classList.remove("hidden");
        switchAuthTab("login");
      });

      let savedTheme = "light";
      try { savedTheme = localStorage.getItem(THEME_KEY) || "light"; } catch {}
      applyTheme(savedTheme);
      syncComposer();
    })();
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/media/<path:filename>")
def serve_media(filename):
    ensure_dirs()
    return send_from_directory(UPLOADS_DIR, os.path.basename(filename))


@app.route("/health")
@app.route("/status")
def health():
    data = load_data()
    return jsonify({"status": "Forge backend running", "total_users": len(data.get("users", {})), "logged_in_user": data.get("logged_in_user")})


@app.route("/presence", methods=["POST"])
def presence():
    data_in = request.get_json(force=True, silent=True) or {}
    db = load_data()
    uname = str(data_in.get("username", "")).strip() or str(db.get("logged_in_user") or "").strip()
    if uname and uname in db.get("users", {}):
        touch_user(db, uname)
        save_data(db)
    return jsonify({"ok": True}), 200


@app.route("/users", methods=["GET"])
def users():
    query = request.args.get("q", "").strip().lower()
    data = load_data()
    user_map = data.get("users", {})
    all_profiles = [public_profile(u, user_map) for u in user_map.values()]
    if not query:
        return jsonify({"users": all_profiles, "total": len(all_profiles)})
    matched = [
        p for p in all_profiles
        if query in " ".join([
            p.get("username", ""), p.get("name", ""), p.get("email", ""),
            p.get("role", ""), p.get("course", ""), p.get("programme", ""),
            p.get("bio", ""), " ".join(p.get("skills", [])),
        ]).lower()
    ]
    return jsonify({"users": matched, "total": len(matched)})


@app.route("/profile/avatar", methods=["POST"])
def update_profile_avatar():
    db = load_data()
    users_dict = db.get("users", {})
    body = request.get_json(silent=True) or {}
    username = (request.form.get("username") or body.get("username") or db.get("logged_in_user") or "").strip()
    if not username or username not in users_dict:
        return jsonify({"error": "User account not found"}), 404

    media_url, err_msg = None, None
    if "avatar" in request.files and request.files["avatar"].filename:
        media_url, err_msg = save_uploaded_file_obj(request.files["avatar"], ("image/",), MAX_IMAGE_BYTES, ".jpg")
    elif body.get("avatar"):
        media_url, err_msg = save_data_url_if_present(body.get("avatar"), ("image/",), MAX_IMAGE_BYTES, ".jpg")

    if err_msg:
        return jsonify({"error": err_msg}), 400
    if not media_url:
        return jsonify({"error": "No image provided"}), 400

    users_dict[username]["avatar"] = media_url
    touch_user(db, username)
    save_data(db)
    return jsonify({"message": "Avatar updated", "profile": public_profile(users_dict[username], users_dict)}), 200


@app.route("/profile/update", methods=["POST"])
def update_profile_details():
    data_in = request.get_json(force=True, silent=True) or {}
    db = load_data()
    users_dict = db.get("users", {})

    current_username = (str(data_in.get("current_username", "")).strip() or str(db.get("logged_in_user") or "").strip())
    if not current_username or current_username not in users_dict:
        return jsonify({"error": "User account not found"}), 404

    user_rec = users_dict[current_username]
    new_name = str(data_in.get("name", user_rec.get("name", ""))).strip()
    new_username = str(data_in.get("username", current_username)).strip()

    if len(new_name) < 2:
        return jsonify({"error": "Name must be at least 2 characters."}), 400
    if not re.match(r"^[A-Za-z0-9_]{3,24}$", new_username):
        return jsonify({"error": "Username must be 3-24 characters (letters, numbers, or underscore only)."}), 400
    if new_username != current_username and new_username in users_dict:
        return jsonify({"error": "That username is already taken."}), 409

    if "role" in data_in:
        role_val = str(data_in.get("role", "")).strip()
        if role_val in {"Student", "Job Creator"}:
            user_rec["role"] = role_val

    if "bio" in data_in and len(str(data_in.get("bio", "")).strip()) >= 5:
        user_rec["bio"] = str(data_in.get("bio", "")).strip()

    if "skills" in data_in:
        skills_list = parse_skills(data_in.get("skills", []))
        if skills_list:
            user_rec["skills"] = skills_list

    user_rec["name"] = new_name
    user_rec["last_seen"] = int(time.time())

    if new_username != current_username:
        user_rec["username"] = new_username
        del users_dict[current_username]
        users_dict[new_username] = user_rec
        for other_u in users_dict.values():
            conns = other_u.get("connections") or []
            if current_username in conns:
                other_u["connections"] = [new_username if c == current_username else c for c in conns]
        for p in db.get("posts", []):
            if p.get("author_username") == current_username:
                p["author_username"], p["author"] = new_username, new_name
            rep_by = p.get("reposted_by") or []
            if current_username in rep_by:
                p["reposted_by"] = [new_username if r == current_username else r for r in rep_by]
        for m in db.get("messages", []):
            if m.get("sender") == current_username:
                m["sender"] = new_username
            if m.get("receiver") == current_username:
                m["receiver"] = new_username
        if db.get("logged_in_user") == current_username:
            db["logged_in_user"] = new_username
    else:
        for p in db.get("posts", []):
            if p.get("author_username") == current_username:
                p["author"] = new_name

    save_data(db)
    return jsonify({"message": "Profile updated", "profile": public_profile(users_dict[new_username], users_dict)}), 200


@app.route("/profile/password", methods=["POST"])
def change_profile_password():
    data_in = request.get_json(force=True, silent=True) or {}
    db = load_data()
    users_dict = db.get("users", {})
    username = (str(data_in.get("username", "")).strip() or str(db.get("logged_in_user") or "").strip())
    current_password = str(data_in.get("current_password", ""))
    new_password = str(data_in.get("new_password", ""))
    confirm_password = str(data_in.get("confirm_password", ""))

    if not username or username not in users_dict:
        return jsonify({"error": "User account not found"}), 404
    if not current_password:
        return jsonify({"error": "Current password is required"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "New password must be at least 6 characters long"}), 400
    if new_password != confirm_password:
        return jsonify({"error": "New password and re-entered password do not match"}), 400

    user_rec = users_dict[username]
    if user_rec.get("password", "") and current_password != user_rec.get("password", ""):
        return jsonify({"error": "Current password is incorrect"}), 401

    user_rec["password"] = new_password
    save_data(db)
    return jsonify({"message": "Password updated successfully"}), 200


@app.route("/signup", methods=["POST"])
def signup():
    data_in = request.get_json(force=True, silent=True) or {}
    role = str(data_in.get("role", "")).strip() or "Student"
    username = str(data_in.get("username", "")).strip()
    password = str(data_in.get("password", ""))
    name = str(data_in.get("name", "")).strip()
    email = str(data_in.get("email", "")).strip()
    course = str(data_in.get("course", "")).strip() or str(data_in.get("programme", "")).strip()
    year = str(data_in.get("year", "")).strip()
    bio = str(data_in.get("bio", "")).strip()
    skills = parse_skills(data_in.get("skills", []))

    if role not in {"Student", "Job Creator"}:
        return jsonify({"error": "Please select Student or Job Creator."}), 400
    if not re.match(r"^[A-Za-z0-9_]{3,24}$", username):
        return jsonify({"error": "Username must be 3-24 characters (letters, numbers, or underscore only)."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long."}), 400
    if len(name) < 2:
        return jsonify({"error": "Please enter a proper full name."}), 400
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    if course not in {"B.Tech", "B.E.", "B.Sc", "BCA"}:
        return jsonify({"error": "Please select a valid course."}), 400
    if year not in {"1", "2", "3", "4"}:
        return jsonify({"error": "Please select a valid year (1 to 4)."}), 400
    if len(bio) < 5:
        return jsonify({"error": "Please enter a proper bio (at least 5 characters)."}), 400
    if not skills:
        return jsonify({"error": "Please provide at least one skill."}), 400

    db = load_data()
    users_dict = db["users"]
    prev_user = users_dict.get(username, {})
    user_rec = {
        "username": username, "password": password, "role": role,
        "name": name, "email": email, "avatar": prev_user.get("avatar", ""),
        "course": course, "programme": course, "year": year,
        "bio": bio, "skills": skills, "connections": prev_user.get("connections", []),
        "last_seen": int(time.time()),
    }
    users_dict[username] = user_rec
    db["logged_in_user"] = username
    save_data(db)
    return jsonify({"message": "Account created", "profile": public_profile(user_rec, users_dict)}), 201


@app.route("/login", methods=["POST"])
def login():
    data_in = request.get_json(force=True, silent=True) or {}
    username = str(data_in.get("username", "")).strip()
    password = str(data_in.get("password", ""))
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    db = load_data()
    users_dict = db["users"]
    if username not in users_dict:
        return jsonify({"error": "Account not found. Please sign up first with proper details."}), 404
    user_rec = users_dict[username]
    if user_rec.get("password", "") and password != user_rec.get("password", ""):
        return jsonify({"error": "Invalid password"}), 401
    user_rec["last_seen"] = int(time.time())
    db["logged_in_user"] = username
    save_data(db)
    return jsonify({"message": "Logged in", "profile": public_profile(user_rec, users_dict)}), 200


@app.route("/logout", methods=["POST"])
def logout():
    db = load_data()
    uname = db.get("logged_in_user")
    if uname and uname in db.get("users", {}):
        db["users"][uname]["last_seen"] = 0
    db["logged_in_user"] = None
    save_data(db)
    return jsonify({"message": "Logged out"}), 200


@app.route("/connect", methods=["POST"])
def connect_user():
    data_in = request.get_json(force=True, silent=True) or {}
    target = str(data_in.get("target_username", "")).strip()
    db = load_data()
    actor = str(data_in.get("username", "")).strip() or db.get("logged_in_user") or ""
    if not actor or actor not in db["users"]:
        return jsonify({"error": "Active user not found"}), 400
    if not target or target not in db["users"]:
        return jsonify({"error": "Target user not found"}), 404
    if actor == target:
        return jsonify({"error": "Cannot follow yourself"}), 400

    touch_user(db, actor)
    conns = db["users"][actor].setdefault("connections", [])
    if target in conns:
        conns.remove(target)
        msg = f"Unfollowed {target}"
    else:
        conns.append(target)
        msg = f"Following {target}"
        add_notification(db, str(data_in.get("actor_name", "")).strip() or db["users"][actor].get("name") or actor, actor)

    save_data(db)
    return jsonify({"message": msg, "following": target in conns, "connections": conns}), 200


@app.route("/posts", methods=["GET", "POST"])
def posts():
    db = load_data()
    if request.method == "GET":
        return jsonify({"posts": db.get("posts", [])}), 200

    is_multipart = bool(request.files) or bool(request.form)
    src = request.form if is_multipart else (request.get_json(force=True, silent=True) or {})
    raw_text = str(src.get("text", ""))
    author_username = str(src.get("author_username", "")).strip() or db.get("logged_in_user") or ""
    author = str(src.get("author", "")).strip() or author_username or "Member"
    headline = str(src.get("headline", "")).strip() or "Forge Member"

    text = raw_text.strip()
    if not text:
        return jsonify({"error": "Post text is required"}), 400
    if len(raw_text) > MAX_POST_CHARS:
        return jsonify({"error": f"Post cannot exceed {MAX_POST_CHARS} characters"}), 400

    safe_images, safe_videos = [], []
    if is_multipart:
        for field, prefixes, max_b, ext, target_list in [
            ("image", ("image/",), MAX_IMAGE_BYTES, ".jpg", safe_images),
            ("video", ("video/",), MAX_VIDEO_BYTES, ".mp4", safe_videos),
        ]:
            if field in request.files and request.files[field].filename:
                url, err = save_uploaded_file_obj(request.files[field], prefixes, max_b, ext)
                if err:
                    return jsonify({"error": err}), 400
                if url:
                    target_list.append(url)
    else:
        for img in (src.get("images") or [])[:1]:
            url, err = save_data_url_if_present(img, ("image/",), MAX_IMAGE_BYTES, ".jpg")
            if err:
                return jsonify({"error": err}), 400
            if url:
                safe_images.append(url)
        for vid in (src.get("videos") or [])[:1]:
            url, err = save_data_url_if_present(vid, ("video/",), MAX_VIDEO_BYTES, ".mp4")
            if err:
                return jsonify({"error": err}), 400
            if url:
                safe_videos.append(url)

    touch_user(db, author_username)
    new_post = {
        "id": next_item_id(db.get("posts", [])),
        "author": author,
        "author_username": author_username,
        "author_avatar": db.get("users", {}).get(author_username, {}).get("avatar", "") if author_username else "",
        "badge": "You",
        "headline": headline,
        "time": "Just now",
        "text": text,
        "likes": 0,
        "comments": 0,
        "comments_list": [],
        "reposts": 0,
        "reposted_by": [],
        "images": safe_images,
        "videos": safe_videos,
    }
    db.setdefault("posts", []).insert(0, new_post)
    add_notification(db, author, author_username)
    save_data(db)
    return jsonify({"message": "Post created", "post": new_post}), 201


@app.route("/posts/<int:post_id>/like", methods=["POST"])
def like_post(post_id):
    data_in = request.get_json(force=True, silent=True) or {}
    action = str(data_in.get("action", "like")).strip().lower()
    db = load_data()
    for p in db.get("posts", []):
        if p.get("id") == post_id:
            cur = int(p.get("likes", 0))
            if action == "unlike":
                p["likes"] = max(0, cur - 1)
            else:
                p["likes"] = cur + 1
                actor_u = str(data_in.get("actor_username", "")).strip() or str(db.get("logged_in_user") or "").strip()
                actor_n = str(data_in.get("actor_name", "")).strip() or db.get("users", {}).get(actor_u, {}).get("name") or actor_u or "Member"
                add_notification(db, actor_n, actor_u)
            save_data(db)
            return jsonify({"id": post_id, "likes": p["likes"]}), 200
    return jsonify({"error": "Post not found"}), 404


@app.route("/posts/<int:post_id>/comment", methods=["POST"])
def comment_post(post_id):
    data_in = request.get_json(force=True, silent=True) or {}
    text = str(data_in.get("text", "")).strip()
    if not text:
        return jsonify({"error": "Comment text is required"}), 400
    db = load_data()
    author = str(data_in.get("author", "")).strip() or str(db.get("logged_in_user") or "Member")
    actor_u = str(data_in.get("actor_username", "")).strip() or str(db.get("logged_in_user") or "").strip()
    for p in db.get("posts", []):
        if p.get("id") == post_id:
            clist = p.setdefault("comments_list", [])
            clist.append({"author": author, "text": text})
            p["comments"] = len(clist)
            add_notification(db, author, actor_u)
            save_data(db)
            return jsonify({"message": "Comment added", "post": p}), 201
    return jsonify({"error": "Post not found"}), 404


@app.route("/posts/<int:post_id>/repost", methods=["POST"])
def repost_post(post_id):
    data_in = request.get_json(force=True, silent=True) or {}
    db = load_data()
    actor = str(data_in.get("username", "")).strip() or str(db.get("logged_in_user") or "Member")
    actor_key = actor.strip().lower()
    actor_name = str(data_in.get("actor_name", "")).strip() or db.get("users", {}).get(actor, {}).get("name") or actor
    for p in db.get("posts", []):
        if p.get("id") == post_id:
            rep = p.setdefault("reposted_by", [])
            if any(str(r).strip().lower() == actor_key for r in rep):
                p["reposted_by"] = [r for r in rep if str(r).strip().lower() != actor_key]
                p["reposts"] = max(0, len(p["reposted_by"]))
                save_data(db)
                return jsonify({"message": "Repost undone", "reposted": False, "post": p}), 200
            rep.append(actor)
            p["reposts"] = len(rep)
            add_notification(db, actor_name, actor)
            save_data(db)
            return jsonify({"message": "Reposted", "reposted": True, "post": p}), 200
    return jsonify({"error": "Post not found"}), 404


@app.route("/posts/<int:post_id>", methods=["DELETE"])
def delete_post(post_id):
    db = load_data()
    orig = db.get("posts", [])
    rem = [p for p in orig if int(p.get("id", -1)) != post_id]
    if len(rem) == len(orig):
        return jsonify({"error": "Post not found"}), 404
    db["posts"] = rem
    save_data(db)
    return jsonify({"message": "Post deleted", "id": post_id}), 200


@app.route("/jobs", methods=["GET", "POST"])
def jobs():
    db = load_data()
    if request.method == "GET":
        return jsonify({"jobs": [j for j in db.get("jobs", []) if str(j.get("title", "")).strip().lower() not in BLOCKED_JOB_TITLES]}), 200
    data_in = request.get_json(force=True, silent=True) or {}
    title = str(data_in.get("title", "")).strip()
    if not title:
        return jsonify({"error": "Job title is required"}), 400
    new_job = {
        "id": next_item_id(db.get("jobs", [])),
        "title": title,
        "company": str(data_in.get("company", "")).strip(),
        "location": str(data_in.get("location", "")).strip(),
        "skills": parse_skills(data_in.get("skills", [])),
    }
    db.setdefault("jobs", []).insert(0, new_job)
    save_data(db)
    return jsonify({"message": "Job created", "job": new_job}), 201


@app.route("/messages", methods=["GET", "POST"])
def messages():
    db = load_data()
    all_msgs = db.get("messages", [])
    if request.method == "GET":
        u1 = str(request.args.get("user1", "")).strip()
        u2 = str(request.args.get("user2", "")).strip()
        if u1 and u2:
            return jsonify({"messages": [m for m in all_msgs if (m.get("sender") == u1 and m.get("receiver") == u2) or (m.get("sender") == u2 and m.get("receiver") == u1)]}), 200
        if u1:
            return jsonify({"messages": [m for m in all_msgs if m.get("sender") == u1 or m.get("receiver") == u1]}), 200
        return jsonify({"messages": all_msgs}), 200

    is_multipart = bool(request.files) or bool(request.form)
    src = request.form if is_multipart else (request.get_json(force=True, silent=True) or {})
    sender = str(src.get("sender", "")).strip() or str(db.get("logged_in_user") or "").strip()
    receiver = str(src.get("receiver", "")).strip()
    text = str(src.get("text", "")).strip()
    sender_name = str(src.get("sender_name", "")).strip()

    image_url = None
    if is_multipart and "image" in request.files and request.files["image"].filename:
        image_url, err = save_uploaded_file_obj(request.files["image"], ("image/",), MAX_CHAT_IMAGE_BYTES, ".jpg")
        if err:
            return jsonify({"error": err}), 400
    elif not is_multipart and src.get("image"):
        image_url, err = save_data_url_if_present(src.get("image"), ("image/",), MAX_CHAT_IMAGE_BYTES, ".jpg")
        if err:
            return jsonify({"error": err}), 400

    if not sender:
        return jsonify({"error": "Sender is required"}), 400
    if not receiver:
        return jsonify({"error": "Please select a recipient"}), 400
    if not text and not image_url:
        return jsonify({"error": "Message text or photo is required"}), 400

    touch_user(db, sender)
    new_msg = {"id": next_item_id(all_msgs), "sender": sender, "receiver": receiver, "text": text, "image": image_url or "", "time": "Just now"}
    db.setdefault("messages", []).append(new_msg)
    add_notification(db, sender_name or db.get("users", {}).get(sender, {}).get("name") or sender, sender)
    save_data(db)
    return jsonify({"message": "Message sent", "data": new_msg}), 201


@app.route("/notifications", methods=["GET"])
def notifications():
    return jsonify({"notifications": load_data().get("notifications", [])}), 200


application = app